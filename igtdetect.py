#!/usr/bin/env python3
# coding=utf-8
import logging
from argparse import ArgumentParser, ArgumentTypeError
from bz2 import BZ2File
from collections import OrderedDict, Iterable
from copy import copy
from functools import partial
from collections import defaultdict, Counter
import glob, sys
from io import TextIOBase
from logging import StreamHandler
from multiprocessing.pool import Pool
from tempfile import NamedTemporaryFile
import pickle

# -------------------------------------------
# Import scikit-learn modules
# -------------------------------------------
from multiprocessing import Lock

import time

from subprocess import Popen, PIPE

from env import *
import re

# -------------------------------------------
# Set up logging
# -------------------------------------------
NORM_LEVEL = 1000
logging.addLevelName(NORM_LEVEL, 'NORMAL')
ERR_LOG = logging.getLogger(name='ERRORS')
STD_LOG = logging.getLogger(name='STD')

stdhandler = StreamHandler(sys.stdout)
errhandler = StreamHandler(sys.stderr)

stdhandler.setLevel(logging.INFO)
errhandler.setLevel(logging.WARNING)

STD_LOG.addHandler(stdhandler)
ERR_LOG.addHandler(errhandler)

# -------------------------------------------
# CONSTANTS
# -------------------------------------------
TYPE_FREKI = 'freki'
TYPE_TEXT = 'text'

# =============================================================================
# FrekiReader
#
# Structure for reading through Freki Files, that does two things primarily:
#
#    1) Loop through the file once first, processing the lines so that
#       previous and following line features can be included for the current line
#
#    2) Make it so that the files can be looped through
#
# =============================================================================

def safe_mode(iterable):
    """
    Like taking the mode of the most common item
    in a sequence, but pick between one of the two
    most frequent if there is no unique mode.

    :param iterable:
    :return:
    """
    items = sorted(Counter(iterable).items(),
                   reverse=True,
                   key=lambda x: x[1])
    return items[0][0] if items else None

class FrekiInfo(object):
    """
    Store a few document-wide pieces of info for
    FrekiDocs, so that they don't need to be
    recalculated each time.
    """
    def __init__(self, fonts=None, llxs=None):
        """
        :type font: FrekiFont
        :type llx: float
        """
        self.def_font = safe_mode(fonts)
        self.llx = safe_mode(llxs)

class FrekiAnalysis(object):
    """
    Wrap the features, labels, and
    full document in an object to output
    from the feature extraction code.
    """
    def __init__(self, data, doc):
        """
        :type data: list[StringInstance]
        :type doc: FrekiDoc
        """
        self.doc = doc
        self.data = data

def get_textfeats(line):
    """
    Given a line as input, return the text-based features
    available for that line.

    :type line: FrekiLine
    :rtype: dict
    """

    # Quick local function to check if a
    # feature is enabled in the config
    # and add it to the feature dict if so.
    feats = {}

    def checkfeat_line(name, func, target=line):
        if name in ENABLED_TEXT_FEATS(conf):
            feats[name] = func(target)

    word_list = list(split_words(line))

    # Quick function to add featuers for words
    # in the line.
    def basic_words():
        for word in word_list:
            if word:
                feats['word_{}'.format(word)] = True

    if T_BASIC in ENABLED_TEXT_FEATS(conf):
        basic_words()

    checkfeat_line(T_HAS_LANGNAME, has_langname)
    checkfeat_line(T_HAS_GRAMS, has_grams)
    checkfeat_line(T_HAS_PARENTHETICAL, has_parenthetical)
    checkfeat_line(T_HAS_CITATION, has_citation)
    checkfeat_line(T_HAS_ASTERISK, has_asterisk)
    checkfeat_line(T_HAS_UNDERSCORE, has_underscore)
    checkfeat_line(T_HAS_BRACKETING, has_bracketing)
    checkfeat_line(T_HAS_QUOTATION, has_quotation)
    checkfeat_line(T_HAS_NUMBERING, has_numbering)
    checkfeat_line(T_HAS_LEADING_WHITESPACE, has_leading_whitespace)
    checkfeat_line(T_HIGH_OOV_RATE, high_en_oov_rate, target=word_list)
    checkfeat_line(T_MED_OOV_RATE, med_en_oov_rate, target=word_list)
    checkfeat_line(T_HAS_JPN, has_japanese)
    checkfeat_line(T_HAS_GRK, has_greek)
    checkfeat_line(T_HAS_KOR, has_korean)
    checkfeat_line(T_HAS_ACC, has_accented_latin)
    checkfeat_line(T_HAS_CYR, has_cyrillic)
    checkfeat_line(T_HAS_DIA, has_diacritic)
    checkfeat_line(T_HAS_UNI, has_unicode)
    checkfeat_line(T_HAS_YEAR, has_year)
    checkfeat_line(T_HIGH_GLS_OOV_RATE, high_gls_oov_rate, target=word_list)
    checkfeat_line(T_HIGH_MET_OOV_RATE, high_met_oov_rate, target=word_list)

    return feats

def get_frekifeats(line, fi):
    """
    :type line: FrekiLine
    :type fi: FrekiInfo
    :rtype: dict
    """
    feats = {}

    # Use this function to check the
    # feature constant name against the
    # list of enabled features, and trigger
    # the appropriate function if it's enabled.
    def checkfeat(name, func):
        if name in ENABLED_FREKI_FEATS(conf):
            feats[name] = func(line, fi)

    # Apply each feature if it is enabled
    checkfeat(F_IS_INDENTED, isindented)
    checkfeat(F_IS_FIRST_PAGE, is_first_page)
    checkfeat(F_PREV_LINE_SAME_BLOCK, prev_line_same_block)
    checkfeat(F_NEXT_LINE_SAME_BLOCK, next_line_same_block)
    checkfeat(F_HAS_NONSTANDARD_FONT, has_nondefault_font)
    checkfeat(F_HAS_SMALLER_FONT, has_smaller_font)
    checkfeat(F_HAS_LARGER_FONT, has_larger_font)

    return feats


def get_all_line_feats(featdict, lineno, **kwargs):
    """
    Given a dictionary mapping lines to features, get
    a new feature dict that includes features for the
    current line, as well as n-1 and n-2 lines, and n+1.

    :rtype: dict
    """

    # Always include the features for the current line.
    cur_feats = featdict[lineno]
    all_feats = copy(cur_feats)

    # Use the features for the line before the previous one (n-2)

    if USE_PREV_PREV_LINE(kwargs):
        prev_prev_feats = featdict.get(lineno - 2, {})
        for prev_key in prev_prev_feats.keys():
            all_feats['prev_prev_' + prev_key] = prev_prev_feats[prev_key]

    # Use the features for the previous line (n-1)
    if USE_PREV_LINE(kwargs):
        prev_feats = featdict.get(lineno - 1, {})
        for prev_key in prev_feats.keys():
            all_feats['prev_' + prev_key] = prev_feats[prev_key]

    # Use the features for the next line (n+1)
    if USE_NEXT_LINE(kwargs):
        next_feats = featdict.get(lineno + 1, {})
        for next_key in next_feats.keys():
            all_feats['next_' + next_key] = next_feats[next_key]

    return all_feats


def _path_rename(path, ext):
    return os.path.splitext(os.path.basename(path))[0] + ext


def get_feat_path(path):
    return os.path.join(FEAT_DIR(args), _path_rename(path, '_feats.txt'))


def get_raw_classification_path(path):
    return os.path.join(os.path.join(DEBUG_DIR(args), 'raw_classifications'),
                        _path_rename(path, '_classifications.txt'))


classified_suffix = '_classified.txt'
detected_suffix = '_detected.txt'


def get_classified_path(path, classified_dir):
    return os.path.join(classified_dir, _path_rename(path, classified_suffix))


def get_detected_path(path, detected_dir):
    return os.path.join(detected_dir, _path_rename(path, detected_suffix))


def get_gold_for_classified(path):
    return os.path.join(GOLD_DIR(args), os.path.basename(path).replace(classified_suffix, '.txt'))


def get_weight_path(path):
    return os.path.join(DEBUG_DIR(args), _path_rename(path, '_weights.txt'))


# -------------------------------------------
# Perform feature extraction.
# -------------------------------------------
def extract_feats(filelist, cw, overwrite=False, skip_noisy=True, **kwargs):
    """
    Perform feature extraction over a list of files.

    Call extract_feat_for_path() in parallel for a speed boost.
    :rtype: list[DataInstance]
    """

    # -------------------------------------------
    # Build a list of measurements from the files.
    # This will be a list of dicts, where each list item
    # represents a line, and each dictionary entry represents
    # a feature:value pair.
    # -------------------------------------------
    data = []

    p = Pool()
    l = Lock()

    def callback(result):
        """:type result: FrekiAnalysis"""
        l.acquire()
        data.extend(result.data)
        l.release()

    for path in filelist:
        # p.apply_async(extract_feats_for_path, args=[path, overwrite, skip_noisy], callback=callback)
        callback(extract_feats_for_path(path, overwrite=overwrite, skip_noisy=skip_noisy, **kwargs))

    p.close()
    p.join()

    # -------------------------------------------
    # Remove the "B/I" from labels if that is disabled.
    # -------------------------------------------
    if not USE_BI_LABELS(conf):
        for datum in data:
            assert isinstance(datum, DataInstance)
            if datum.label[0:2] in ['B-', 'I-']:
                datum.label = datum.label[2:]

    # -------------------------------------------
    # Turn the extracted feature dict into vectors for sklearn.
    # -------------------------------------------
    return data


def load_feats(path):
    """
    Load features from a saved svm-lite like file
    :rtype:
    """
    instances = []
    with open(path, 'r') as feat_f:
        for line in feat_f:
            line_feats = {}
            data = line.split()
            label = data[0]
            for feat, value in [pair.split(':') for pair in data[1:]]:
                line_feats[feat] = bool(value)

            di = DataInstance(label, line_feats)
            instances.append(di)
    return instances


def extract_feats_for_path(path, overwrite=False, skip_noisy=True, **kwargs):
    """
    Perform feature extraction for a single file.

    The output files are in svmlight format, namely:

        LABEL   feature_1:value_1   feature_2:value_2 ...etc

    The "skip_noisy" parameter is intended for training data that
    was created automatically, and for which the labels were mapped,
    but seem unlikely to be correct. Such noisy labels are preceded by
    an asterisk.

    :rtype: FrekiAnalysis
    """
    feat_path = get_feat_path(path)

    path_rel = os.path.abspath(path)
    feat_rel = os.path.abspath(feat_path)

    # -------------------------------------------
    # Create a list of measurements, and associated labels.
    # -------------------------------------------

    # Read in the freki document, whether or
    # not the features need to be reprocessed.
    fd = FrekiDoc.read(path)


    # -------------------------------------------
    # Skip generating the text feature for this path
    # if it's already been generated and the user
    # has not asked to overwrite them.
    # -------------------------------------------
    if os.path.exists(feat_path) and (not overwrite):
        ERR_LOG.warning('File "{}" already generated, not regenerating (use -f to force)...'.format(feat_path))

        line_instances = load_feats(feat_path)

    else:
        line_instances = []

        STD_LOG.info('Opening file "{}" for feature extraction to file "{}"...'.format(path_rel, feat_rel))

        os.makedirs(os.path.dirname(feat_path), exist_ok=True)
        with open(feat_path, 'w', encoding='utf-8') as train_f:

            fi = FrekiInfo(fonts=fd.fonts(),
                           llxs=fd.llxs())

            # 1) Start by getting the features for this
            #    particular line...
            feat_dict = {}
            for line in fd.lines():
                feat_dict[line.lineno] = get_textfeats(line)
                feat_dict[line.lineno].update(get_frekifeats(line, fi))

            # 2) Now, add the prev/next line data as necessary
            for line in fd.lines():
                # Skip noisy (preceded with '*') tagged lines
                label = line.tag
                if label.startswith('*'):
                    if skip_noisy: continue
                    else: label = label.replace('*', '')

                # Strip flags and multiple tags if
                # needed
                label = fix_label_flags_multi(label)

                if 'O' not in label:
                    prev_line = line.doc.get_line(line.lineno-1)
                    if (line.span_id and prev_line and
                            prev_line.span_id and
                            line.span_id == prev_line.span_id):
                        bi_status = 'I'
                    else:
                        bi_status = 'B'

                    label = '{}-{}'.format(bi_status, label)

                all_feats = get_all_line_feats(feat_dict, line.lineno, **kwargs)
                li = DataInstance(label, all_feats)
                line_instances.append(li)

                write_training_vector(li, train_f)

    return FrekiAnalysis(line_instances, fd)


def write_training_vector(li, out=sys.stdout):
    """
    :type li: StringInstance
    :type out: TextIOBase
    """
    out.write('{:s}'.format(li.label))
    for feat in sorted(li.feats.keys()):
        val = li.feats[feat]
        val_str = 1 if val else 0
        if val_str:
            out.write('\t{}:{}'.format(feat, val_str))
    out.write('\n')


# =============================================================================
# FEATURES
# =============================================================================
def isindented(line, fi):
    """
    :type line: FrekiLine
    :type fi: FrekiInfo
    :rtype: bool
    """
    # Is the line's indenting greater than that
    # for the overall document.
    return line.block.llx > fi.llx

def has_smaller_font(line, fi):
    """
    :type line: FrekiLine
    :type fi: FrekiInfo
    :rtype: bool
    """
    for font in line.fonts:
        if font.f_size < fi.def_font.f_size:
            return True
    return False

def has_larger_font(line, fi):
    """
    :type line: FrekiLine
    :type fi: FrekiInfo
    :rtype: bool
    """
    for font in line.fonts:
        if font.f_size > fi.def_font.f_size:
            return True
    return False

def has_nondefault_font(line, fi):
    """
    :type line: FrekiLine
    :type fi: FrekiInfo
    :rtype: bool
    """
    # Get the "default" font
    return bool(set(line.fonts) - set([fi.def_font]))


def has_grams(line):
    """
    :type line: str
    :rtype: bool
    """
    return bool(gram_list and bool(line.search('|'.join(gram_list), flags=re.I)) or
                gram_list_cased and line.search('|'.join(gram_list_cased)))


def has_parenthetical(line):
    """
    :type line: str
    :rtype: bool
    """
    return bool(line.search('\(.*\)'))


# Cover four-digit numbers from 1800--2019
year_str = '(?:1[8-9][0-9][0-9]|20[0-1][0-9])'


def has_citation(line):
    """
    :type line: str
    :rtype: bool
    """
    return bool(line.search('\([^,]+, {}\)'.format(year_str)))


def has_year(line):
    """
    :type line: FrekiLine
    :rtype: bool
    """
    return bool(line.search(year_str))


def has_asterisk(line):
    """
    :type line: FrekiLine
    :rtype: bool
    """
    return '*' in line


def has_underscore(line):
    """
    :type line: FrekiLine
    :rtype: bool
    """
    return '_' in line


def has_bracketing(line):
    """
    :type line: FrekiLine
    :rtype: bool
    """
    return bool(line.search('\[.*\]'))


def has_numbering(line):
    """
    :type line: FrekiLine
    :rtype: bool
    """
    return bool(line.search('^\s*\(?[0-9a-z]+[\)\.]'))


def has_leading_whitespace(line):
    """
    :type line: FrekiLine
    :rtype: bool
    """
    return bool(line.search('^\s+'))


# -------------------------------------------
# Various Unicode Ranges
# -------------------------------------------

def has_cyrillic(line):
    """
    :type line: FrekiLine
    :rtype: bool
    """
    return bool(line.search('[\u0400-\u04FF]', flags=re.UNICODE))


def has_diacritic(line):
    """
    :type line: FrekiLine
    :rtype: bool
    """
    return bool(line.search('[\u0300–\u036F]|[\u1AB0-\u1AFF]|[\u1DC0-\u1DFF]|[\u20D0-\u20FF]|[\uFE20-\uFE2F]',
                            flags=re.UNICODE))


def has_greek(line):
    """
    :type line: FrekiLine
    :rtype: bool
    """
    return bool(line.search('[\u0370-\u03FF]|[\u1F00-\u1FFF]', flags=re.UNICODE))


def has_japanese(line):
    """
    :type line: FrekiLine
    """
    has_kanji = bool(line.search('[\u4E00-\u9FBF]', flags=re.U))
    has_hiragana = bool(line.search('[\u3040-\u309F]', flags=re.U))
    has_katakana = bool(line.search('[\u30A0-\u30FF]', flags=re.U))
    return has_kanji or has_hiragana or has_katakana


def has_accented_latin(line):
    """
    :type line: FrekiLine
    """
    return bool(line.search('[\u00C0-\u00FF]', flags=re.U))


def has_korean(line):
    """:type line: FrekiLine"""
    return bool(line.search('[\uAC00-\uD7A3]', flags=re.U))


def has_unicode(line):
    """:type line: FrekiLine"""
    cyr = has_cyrillic(line)
    dia = has_diacritic(line)
    grk = has_greek(line)
    jpn = has_japanese(line)
    kor = has_korean(line)
    acc = has_accented_latin(line)
    return cyr or dia or grk or jpn or acc or kor


# -------------------------------------------

word_re = re.compile('(\w+)', flags=re.UNICODE)


def clean_word(s):
    w_match = word_re.findall(s)
    return w_match


# -------------------------------------------
# OOV Rate Functions
#
# Use a set threshold to decide at what
# ratio of OOV words to In-Vocabulary words
# constitutes being too dissimilar.
# -------------------------------------------

def med_en_oov_rate(words):
    """:type words: FrekiLine"""
    return HIGH_OOV_THRESH(conf) > oov_rate(en_wl, words) > MED_OOV_THRESH(conf)


def high_en_oov_rate(words):
    """:type words: FrekiLine"""
    return oov_rate(en_wl, words) >= HIGH_OOV_THRESH(conf)


def high_gls_oov_rate(words):
    """:type words: FrekiLine"""
    return oov_rate(gls_wl, words) > HIGH_OOV_THRESH(conf)


def high_met_oov_rate(words):
    """:type words: FrekiLine"""
    return oov_rate(gls_wl, words) > HIGH_OOV_THRESH(conf)


def oov_rate(wl, words):
    """:type wl: WordlistFile
    :type words: FrekiLine
    """
    if not wl:
        return 0.0
    else:

        oov_words = Counter([w in en_wl for w in words])
        c_total = sum([v for v in oov_words.values()])

        if not c_total:
            return 0.0
        else:
            oov_rate = oov_words[False] / c_total
            return oov_rate


# -------------------------------------------
# Read the language names into the global "langs" variable.
# -------------------------------------------
langs = set([])
def init_langnames():
    global langs
    if len(langs) == 0:
        with open(LNG_NAMES(conf), 'r', encoding='utf-8') as f:
            for line in f:
                last_col = ' '.join(line.split()[3:])
                for langname in last_col.split(','):
                    langname = langname.replace('[', '')
                    if len(langname) >= 5:
                        langs.add(langname.lower())


lang_re = re.compile('({})'.format('|'.join(langs), flags=re.I))


def has_langname(line):
    """
    :type line: FrekiLine
    """
    init_langnames()
    return bool(line.search(lang_re))


def has_quotation(line):
    """
    :type line: FrekiLine
    """
    """ Return true if the line in question surrounds more than one word in quotes """
    return bool(line.search('[\'\"‘`“]\S+\s+.+[\'\"’”]'))


def is_first_page(line, *args):
    """:type line: FrekiLine"""
    return line.block.page == 1

def same_block(cur_line, other_line):
    if other_line is None:
        return False
    else:
        return cur_line.block.block_id == other_line.block.block_id

def prev_line_same_block(line, *args):
    """:type line: FrekiLine"""
    prev_line = line.doc.get_line(line.lineno-1)
    return same_block(line, prev_line)

def next_line_same_block(line, *args):
    """:type line: FrekiLine"""
    next_line = line.doc.get_line(line.lineno+1)
    return same_block(line, next_line)

# -------------------------------------------
# TRAIN THE CLASSIFIER
# -------------------------------------------


def label_sort(l):
    order = ['O', 'B', 'I', 'L', 'L-T', 'G', 'T', 'M']
    if l in order:
        return order.index(l)
    else:
        return float('inf')


class ClassifierInfo():
    """
    This is a class for storing information about
    the mallet classifier, such as which features are
    the most informative, and what labels are among
    those that are expected to be seen.
    """

    def __init__(self):
        self.featdict = defaultdict(partial(defaultdict, float))
        self.labels = set([])

    def add_feat(self, label, feat, weight):
        self.featdict[feat][label] = float(weight)
        self.labels.add(label)

    def write_features(self, out=sys.stdout, limit=30):

        vals = []
        defaults = []
        for feat in self.featdict.keys():
            for label, val in self.featdict[feat].items():
                if feat == '<default>':
                    defaults.append((feat, label, val))
                else:
                    vals.append((feat, label, val))

        defaults = sorted(defaults, key=lambda x: label_sort(x[1]))

        # If limit is None, set it to dump all features.
        if limit is None:
            limit = len(vals)

        vals = sorted(vals, key=lambda x: abs(x[2]), reverse=True)[:limit]

        longest_featname = max([len(x[0]) for x in vals])
        longest_label = max([len(x[1]) for x in vals] + [5])

        format_str = '{{:{}}}\t{{:{}}}\t{{:<5.6}}\n'.format(longest_featname, longest_label)

        out.write(format_str.format('feature', 'label', 'weight'))
        linesep = '-' * (longest_featname + longest_label + 10) + '\n'
        out.write(linesep)
        for d in defaults:
            out.write(format_str.format(*d))
        out.write(linesep)
        for val in vals:
            out.write(format_str.format(*val))


def combine_feat_files(pathlist, out_path=None):
    """

    :param pathlist:
    :param out_path:
    :return:
    """
    # Create the training file.

    if out_path is None:
        combined_f = NamedTemporaryFile(mode='w', encoding='utf-8', delete=False)
        out_path = combined_f.name
    else:
        combined_f = open(out_path, 'w', encoding='utf-8')

    # -------------------------------------------
    # 1) Combine all the instances in the files...
    # -------------------------------------------
    for path in pathlist:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                combined_f.write(line)
    combined_f.close()
    return out_path


# =============================================================================
# Train the classifier given a list of files
# =============================================================================
def train_classifier(cw, data, classifier_path=None, debug_on=False,
                     max_features=None, **kwargs):
    """
    Train the classifier based on the input files in filelist.

    :type cw: ClassifierWrapper
    :type data: list[DataInstance]
    :type max_features: int
    """


    if max_features is not None:
        max_features = int(max_features)
    else:
        max_features = -1



    start_time = time.time()


    cw.train(data, num_feats=max_features)
    stop_time = time.time()
    ERR_LOG.log(NORM_LEVEL,
                'Training finished in "{}" seconds.'.format(
                    stop_time - start_time))

    # Save the classifier.
    ERR_LOG.log(NORM_LEVEL, 'Writing classifier out to "{}"'.format(classifier_path))
    cw.save(classifier_path)

def assign_spans(fd):
    """
    Assign span IDs to a document without them,
    assuming only that a span is a contiguous
    block of non-'O' labels.

    :param fd: Document to assign span_ids to
    :type fd: FrekiDoc
    """
    num_spans = 0
    last_tag = 'O'
    for line in fd.lines():
        if 'O' not in line.tag:

            # Increment if the last tag
            # was 'O'
            if 'O' in last_tag:
                num_spans += 1

            line.span_id = 's{}'.format(num_spans)
        else:
            line.span_id = None

        last_tag = line.tag


# =============================================================================
# Evaluation Calculations
# =============================================================================
def exact_span_matches(eval_spans, gold_spans):
    """
    The exact span matches are the intersections between

    :type eval_spans: OrderedDict
    :type gold_spans: OrderedDict
    """
    return len(set(eval_spans.values()) & set(gold_spans.values()))

def f_measure(p, r):
    return 2 * (p*r)/(p+r) if (p+r) > 0 else 0

def partial_matches(eval_spans, gold_spans, mode):
    """
    The partial span precision is calculated by the number of system spans which overlap
    in some way with a system span.

    :type eval_spans: OrderedDict
    :type gold_spans: OrderedDict
    """
    matches = 0

    if mode == 'precision':
        for sys_start, sys_stop in [(s[0], s[-1]) for s in eval_spans.values()]:
            for gold_start, gold_stop in [(s[0], s[-1]) for s in gold_spans.values()]:

                # We define a partial match by whether either the start or stop index of
                # the system span occurs within the [start,stop] range of at least one gold span.
                if (gold_stop >= sys_start >= gold_start) or (gold_stop >= sys_stop >= gold_start):
                    matches += 1
                    break
    elif mode == 'recall':
        for gold_start, gold_stop in [(s[0], s[-1]) for s in gold_spans.values()]:
            for sys_start, sys_stop in [(s[0], s[-1]) for s in eval_spans.values()]:
                if (sys_stop >= gold_start >= sys_start) or (sys_stop >= gold_stop >= sys_start):
                    matches += 1
                    break

    return matches


class Evaluator(object):
    def __init__(self):
        self.se = SpanEvaluator()
        self.le = LabelEvaluator()

class SpanEvaluator(object):
    def __init__(self):
        self.exact_matches = 0

        # Matches are calculated differently for
        # precision and recall, since otherwise
        # recall could be >1.0
        self.partial_precision_matches = 0
        self.partial_recall_matches = 0

        self.gold_spans = 0
        self.system_spans = 0

    def add_spans(self, eval_spans, gold_spans):
        self.exact_matches += exact_span_matches(eval_spans, gold_spans)
        self.partial_precision_matches += partial_matches(eval_spans, gold_spans, 'precision')
        self.partial_recall_matches += partial_matches(eval_spans, gold_spans, 'recall')

        self.gold_spans += len(gold_spans)
        self.system_spans += len(eval_spans)

    def exact_precision(self): return self.exact_matches / self.system_spans
    def exact_recall(self): return self.exact_matches / self.gold_spans
    def exact_fmeasure(self): return f_measure(self.exact_precision(), self.exact_recall())
    def exact_prf(self): return self.exact_precision(),self.exact_recall(),self.exact_fmeasure()

    def partial_precision(self): return self.partial_precision_matches / self.system_spans
    def partial_recall(self): return self.partial_recall_matches / self.gold_spans
    def partial_fmeasure(self): return f_measure(self.partial_precision(), self.partial_recall())
    def partial_prf(self): return self.partial_precision(), self.partial_recall(), self.partial_fmeasure()


class LabelEvaluator(object):
    """
    This is a utility class that helps calculate
    performance over spans of IGT lines, rather
    than the per-line accuracies, which are in
    some ways less helpful.
    """

    def __init__(self):
        self._matrix = defaultdict(partial(defaultdict, int))

                           # Matches,System,Gold




    def add_eval_pair(self, gold, guess):
        """
        For a given line number, catalog it.
        """
        self._matrix[gold][guess] += 1

        self.last_guess = guess
        self.last_gold = gold

    def _matches(self, exclude=list()):
        return [self._matrix[gold][gold] for gold in self._labels() if gold not in exclude]

    def _gold_sums(self, exclude=list()):
        gold_totals = defaultdict(int)
        for gold in self._matrix.keys():
            if gold in exclude:
                continue
            for guess in self._matrix[gold]:
                gold_totals[gold] += self._matrix[gold][guess]
        return [gold_totals[l] for l in self._labels()]

    def _guess_sums(self, exclude=list()):
        guess_totals = defaultdict(int)
        for gold in self._matrix.keys():
            for guess in self._matrix[gold]:
                if guess in exclude:
                    continue
                guess_totals[guess] += self._matrix[gold][guess]

        return [guess_totals[l] for l in self._labels()]

    def _recalls(self):
        return [matches / sums if sums > 0 else 0 for matches, sums in zip(self._matches(), self._gold_sums())]

    def _labels(self):
        return sorted(set(self._matrix.keys()) | set(
            [inner_key for outer_key in self._matrix.keys() for inner_key in self._matrix[outer_key].keys()]),
                      key=label_sort)

    # -------------------------------------------
    # Functions for calculate per-label
    # precision, recall, and f-measure, optionally
    # excluding certain labels.
    # -------------------------------------------

    def recall(self, exclude=list()):
        num = sum(self._matches(exclude))
        den = sum(self._gold_sums(exclude))
        return num / den if den > 0 else 0

    def precision(self, exclude=list()):
        """
        Calculate label precision
        """
        num = sum(self._matches(exclude))
        den = sum(self._guess_sums(exclude))
        return num / den if den > 0 else 0

    def prf(self, exclude=list()):
        return (self.precision(exclude), self.recall(exclude), self.f_measure(exclude))

    def f_measure(self, exclude=list()):
        denom = self.precision(exclude) + self.recall(exclude)
        if denom == 0:
            return 0
        else:
            return 2 * (self.precision(exclude) * self.recall(exclude)) / denom

    def _vals(self):
        return [[self._matrix[gold][label] for gold in self._labels()] for label in self._labels()]

    def matrix(self, csv=False):
        # Switch the delimiter from tab to comma
        # if using a csv format.
        delimiter = '\t'
        if csv:
            delimiter = ','

        ret_str = '{} COLS: Gold --- ROWS: Predicted\n'.format(delimiter)
        ret_str += delimiter.join([''] + ['{:4}'.format(l) for l in self._labels()]) + '\n'
        for label in self._labels():
            vals = [self._matrix[gold][label] for gold in self._labels()]
            matches = self._matrix[label][label]
            compares = sum(vals)
            precision = matches / compares if compares > 0 else 0
            ret_str += delimiter.join([label] + ['{:4}'.format(v) for v in vals] + ['{:.2f}'.format(precision)]) + '\n'

        ret_str += delimiter.join([''] + ['{:4.2f}'.format(r) for r in self._recalls()]) + '\n'
        return ret_str


# =============================================================================
# Testing (Apply Classifier to new Documents)
# =============================================================================

def classify_docs(filelist, classifier_path=None, overwrite=None, debug_on=False,
                  classified_dir=None, detected_dir=None,
                  **kwargs):
    feat_paths = [get_feat_path(p) for p in filelist]

    if not feat_paths:
        ERR_LOG.critical("No text vector files were found.")
        sys.exit()

    # Load the saved classifier...
    STD_LOG.log(NORM_LEVEL, "Loading saved classifier...")
    cw = ClassifierWrapper.load(classifier_path)
    STD_LOG.log(NORM_LEVEL, "Classifier Loaded.")

    for path, feat_path in zip(filelist, feat_paths):

        # -------------------------------------------
        # Open the output classification path
        # -------------------------------------------
        fa = extract_feats_for_path(path, overwrite=overwrite, skip_noisy=True, **kwargs)

        if not fa.data:
            continue

        classifications = cw.test(fa.data)



        # Ensure that the number of lines in the feature file
        # matches the number of lines returned by the classifier
        num_lines = len(fa.data)
        num_classifications = len(classifications)

        if num_lines != num_classifications:
            ERR_LOG.critical(
                "The number of lines ({}) does not match the number of classifications ({}). Skipping file {}".format(
                    num_lines, num_classifications, path))
            continue

        # -------------------------------------------
        # Get ready to write the classified IGT instances out.
        # The "classified_dir" is for the full files, with "O"
        # lines, the "detected_dir" is only for contiguous, non-O lines.
        # -------------------------------------------
        if classified_dir:
            os.makedirs(classified_dir, exist_ok=True)
            classified_f = open(get_classified_path(path, classified_dir), 'w', encoding='utf-8')

        if detected_dir:
            os.makedirs(detected_dir, exist_ok=True)
            detected_f = open(get_detected_path(path, detected_dir), 'w', encoding='utf-8')

        # -------------------------------------------

        # This file will contain the raw labelings from the classifier.
        if debug_on:
            os.makedirs(os.path.dirname(get_raw_classification_path(path)), exist_ok=True)
            ERR_LOG.log(NORM_LEVEL, 'Writing out raw classifications "{}"'.format(get_raw_classification_path(path)))
            raw_classification_f = open(get_raw_classification_path(path), 'w')

        # -------------------------------------------
        # Iterate through the returned classifications
        # and assign them to the lines in the test file.
        #
        # Optionally, write out the raw classification distribution.
        # -------------------------------------------
        cur_span = []
        total_detected = 0

        old_lines = list(fa.doc.lines())

        for line, classification in zip(old_lines, classifications):

            # Write the line number and classification probabilities to the debug file.
            if debug_on:
                raw_classification_f.write('{}:'.format(line.lineno))
                for label, weight in classification:
                    raw_classification_f.write('\t{}  {:.3e}'.format(label, weight))
                raw_classification_f.write('\n')
                raw_classification_f.flush()

            # -------------------------------------------
            # Get the best label
            # -------------------------------------------
            best_label = sorted(classification, key=lambda x: x[1], reverse=True)[0][0]

            # -------------------------------------------
            # If we are using B+I labels, make sure to
            # strip them off before writing out the file.
            # -------------------------------------------
            if best_label[0:2] in set(['I-', 'B-']):
                best_label = best_label[2:]

            # Set the label for the line in the working block
            # before potentially writing it out.
            fl = FrekiLine(line,
                           tag=best_label,
                           line=line.lineno)
            fl.fonts = line.fonts

            fa.doc.set_line(line.lineno, fl)


            if best_label == 'O' and detected_dir:
                if cur_span:
                    detected_f.write('\n'.join(cur_span))
                    detected_f.write('\n\n')
                    cur_span = []
                    total_detected += 1
            else:
                cur_span.append('{:<8}{}'.format(best_label, fl))

        # Write out the classified file.
        if classified_dir:
            assign_spans(fa.doc)
            classified_f.write(str(fa.doc))
            classified_f.close()

        if detected_dir:
            detected_f.close()
            if total_detected == 0:
                os.unlink(get_detected_path(path, detected_dir))

        if debug_on:
            raw_classification_f.close()


def eval_files(filelist, out_path, csv, gold_dir=None, **kwargs):
    """
    Given a list of target files, evaluate them against
    the files given in the gold dir.

    If the gold dir does not exist, or does not contain
    the specified file, make sure to log an error.
    """
    # Set up the output stream
    if out_path is None:
        out_f = sys.stdout
    else:
        out_f = open(out_path, 'w')

    if not os.path.exists(gold_dir):
        ERR_LOG.critical('The gold file directory "{}" is missing or is unavailable.'.format(GOLD_DIR(conf)))
        sys.exit(2)
    elif not os.path.isdir(gold_dir):
        ERR_LOG.error('The gold file directory "{}" appears to be a file, not a directory.'.format(GOLD_DIR(conf)))
        sys.exit(2)

    # Create the counter to iterate over all the files.

    ev = Evaluator()
    old_se = SpanEvaluator() # <-- for evaluating old-style (autogenerated) spans

    for eval_path in filelist:
        gold_path = get_gold_for_classified(eval_path)
        if not os.path.exists(gold_path):
            ERR_LOG.warning('No corresponding gold file was found for the evaluation file "{}"'.format(eval_path))
        else:
            eval_file(eval_path, gold_path, ev=ev, old_se=old_se)

    # Now, write out the sc results.
    delimiter = '\t'
    if csv:
        delimiter = ','
    out_f.write(ev.le.matrix() + '\n')

    out_f.write('----- Labels -----\n')
    out_f.write(' Classifiation Acc: {:.2f}\n'.format(ev.le.precision()))
    out_f.write('       Non-O P/R/F: {}\n\n'.format(delimiter.join(['{:.2f}'.format(x) for x in ev.le.prf(['O'])])))
    out_f.write('----- Spans ------\n')
    out_f.write(
        '  Exact-span P/R/F: {}\n'.format(delimiter.join(['{:.2f}'.format(x) for x in ev.se.exact_prf()])))
    out_f.write(
        'Partial-span P/R/F: {}\n'.format(delimiter.join(['{:.2f}'.format(x) for x in ev.se.partial_prf()])))
    out_f.write('\n--- Auto-Spans ---\n')
    out_f.write(
        '  Exact-span P/R/F: {}\n'.format(delimiter.join(['{:.2f}'.format(x) for x in old_se.exact_prf()])))
    out_f.write(
        'Partial-span P/R/F: {}\n'.format(delimiter.join(['{:.2f}'.format(x) for x in old_se.partial_prf()])))


    out_f.close()

def fix_label_flags_multi(label):
    if STRIP_FLAGS(conf):
        label = label.split('+')[0]

    if not USE_MULTI_LABELS(conf):
        label = label.split('-')[0]

    return label


def eval_file(eval_path, gold_path, ev=None, old_se=None, outstream=sys.stdout):
    """
    Look for the filename that matches the specified file
    """
    eval_fd = FrekiDoc.read(eval_path)
    gold_fd = FrekiDoc.read(gold_path)

    if len(eval_fd) != len(gold_fd):
        ERR_LOG.error(
            'The evaluation file "{}" and the gold file "{}" appear to have a different number of lines. Evaluation aborted.'.format(
                eval_path, gold_path))
    else:
        if ev is None:
            ev = Evaluator()
        if old_se is None:
            old_se = SpanEvaluator()

        # -------------------------------------------
        # Compare the labels across lines.
        # -------------------------------------------
        for line in eval_fd.lines():
            eval_label = fix_label_flags_multi(eval_fd.get_line(line.lineno).tag)
            gold_label = fix_label_flags_multi(gold_fd.get_line(line.lineno).tag)
            ev.le.add_eval_pair(gold_label, eval_label)

        # -------------------------------------------
        # Compare spans
        # -------------------------------------------
        gold_spans = gold_fd.spans()
        eval_spans = eval_fd.spans()

        ev.se.add_spans(eval_spans, gold_spans)

        # -------------------------------------------
        # Do old-style comparison, ignoring span_id and
        # assigning span id to non-contiguous...
        # -------------------------------------------
        assign_spans(gold_fd)
        assign_spans(eval_fd)

        old_style_gold_spans = gold_fd.spans()
        old_style_eval_spans = eval_fd.spans()

        old_se.add_spans(old_style_eval_spans, old_style_gold_spans)

        return ev, old_se

def flatten(seq):
    """:rtype: Iterable"""
    flat = []
    if not (isinstance(seq, list) or isinstance(seq, tuple)):
        return [seq]
    else:
        for elt in seq:
            flat.extend(flatten(elt))
        return flat

# -------------------------------------------
# ARG TYPES
# -------------------------------------------
def globfiles(pathname):
    """:rtype: Iterable[str]"""
    g = glob.glob(pathname)
    if not g:
        raise ArgumentTypeError(
            'No files found matching pattern "{}".\nCheck that the path is valid and that containing directories exist.'.format(
                pathname))
    else:
        paths = []
        for path in g:
            if os.path.isdir(path):
                paths.extend([os.path.join(path, p) for p in os.listdir(path)])
            else:
                paths.append(path)
        return paths

def split_words(sent):
    for w_m in re.finditer('\w+', sent, flags=re.UNICODE):
    # for w_m in re.finditer('[^\.\-\s]+', sent):
        w = w_m.group(0).lower()
        # The '#' and ':' characters are reserved in SVMlite format
        yield w.replace(':','').replace('#','')



def true_val(s):
    """:type s: str
    :rtype: bool"""
    if str(s).lower() in ['1', 'on', 't', 'true', 'enabled']:
        return True
    elif str(s).lower() in ['0', 'off', 'f', 'false', 'disabled']:
        return False

# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    # -------------------------------------------
    # Set up the main argument parser (for subcommands)
    # -------------------------------------------
    main_parser = ArgumentParser()

    # -------------------------------------------
    # Set up common parser options shared by
    # the subcommands
    # -------------------------------------------
    common_parser = ArgumentParser(add_help=False)
    common_parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbosity.')
    common_parser.add_argument('-c', '--config', help='Alternate config file.')
    common_parser.add_argument('-f', '--overwrite-features', dest='overwrite', action='store_true',
                               help='Overwrite previously generated feature files.')
    common_parser.add_argument('--profile', help='Performance profile the app.', action='store_true')

    # -------------------------------------------
    # Load the default config file.
    # -------------------------------------------
    conf = PathRelativeConfigParser()
    def_path = os.path.join(os.path.dirname(__file__), './defaults.ini')
    if os.path.exists(def_path):
        conf.read(def_path)


    for sec in conf.sections():
        common_parser.set_defaults(**conf[sec])

    # -------------------------------------------
    # Append extra config file onto args.
    # -------------------------------------------
    known_args = common_parser.parse_known_args()[0]
    alt_c = PathRelativeConfigParser()
    if known_args.config and os.path.exists(known_args.config):
        alt_c.read(known_args.config)
        conf.update(alt_c)

    # -------------------------------------------
    # Try to add things from the pythonpath
    # -------------------------------------------
    pythonpath = conf.get('runtime', 'pythonpath', fallback=None)
    if pythonpath:
        for subpath in pythonpath.split(':'):
            sys.path.append(subpath)

    # -------------------------------------------
    # Import non-default modules
    # -------------------------------------------
    from freki.serialize import FrekiDoc, FrekiLine, FrekiFont
    import numpy as np
    from rgclassifier.models import ClassifierWrapper, StringInstance, DataInstance


    # -------------------------------------------
    # Function to return whether an option is required,
    # or whether it's been specified somewhere in
    # the config file already.
    # -------------------------------------------
    def requires_opt(sec, opt, exists=False):
        ret_val = not (conf and
                       conf.has_option(sec, opt) and
                       (not exists or os.path.exists(conf.get(sec, opt))))
        return ret_val

    # -------------------------------------------
    # Define a few methods to help dealing with
    # whether or not to prompt the user for an
    # argument, or whether it's already been specified
    # in the config file.
    # -------------------------------------------
    def requires_path(opt, exists=False):
        return requires_opt('paths', opt, exists=exists)

    def requires_glob(opt):
        return not bool([p for p in get_glob(opt) if os.path.exists(p)])

    def get_path(opt, fallback=None):
        return conf.get('paths', opt, fallback=fallback)

    def get_glob(opt):
        return glob.glob(get_path(opt, fallback=''))

    # -------------------------------------------
    # Set up a common parser to inherit for the functions
    # that require the classifier to be specified
    # -------------------------------------------
    tt_parser = ArgumentParser(add_help=False)
    tt_parser.add_argument('--classifier-path', required=requires_path('classifier_path', exists=False),
                           help='Path to the saved classifier model.', default=get_path('classifier_path'))

    # Parser for combining evaluation arguments.
    ev_parser = ArgumentParser(add_help=False)
    ev_parser.add_argument('-o', '--output', dest='out_path', help='Output path to write result. [Default: stdout]')
    ev_parser.add_argument('--csv', help='Format the output as CSV.')
    ev_parser.add_argument('--eval-files', help='Files to evaluate against',
                           required=requires_glob('eval_files'),
                           default=get_glob('eval_files'),
                           type=globfiles)
    ev_parser.add_argument('--gold-dir', default=conf.get('paths', 'gold_dir', fallback=None), required=requires_opt('paths', 'gold_dir'))

    # -------------------------------------------
    # Set up the subcommands
    # -------------------------------------------
    subparsers = main_parser.add_subparsers(help='Valid subcommands', dest='subcommand')
    subparsers.required = True

    # -------------------------------------------
    # TRAINING
    # -------------------------------------------
    train_p = subparsers.add_parser('train', parents=[common_parser, tt_parser])
    train_p.add_argument('--use-bi-labels', type=int, default=conf.get('labels', 'use_bi_labels', fallback=1))
    train_p.add_argument('--max-features', type=int, default=-1)
    train_p.add_argument('--train-files', help='Path to the files for training the classifier.',
                         required=requires_glob('train_files'),
                         default=get_glob('train_files'),
                         type=globfiles)
    train_p.add_argument('--overwrite-model', help='Overwrite previously created models', action='store_true')


    # -------------------------------------------
    # TESTING
    # -------------------------------------------
    test_p = subparsers.add_parser('test', parents=[common_parser, tt_parser])
    test_p.add_argument('--test-files', help='Path to the files to be classified.', type=globfiles,
                        required=requires_glob('test_files'),
                        default=get_glob('test_files'))
    test_p.add_argument('--classified-dir', help='Directory to output the classified documents.',
                        required=requires_path('classified_dir'),
                        default=get_path('classified_dir'))

    # -------------------------------------------
    # EVAL
    # -------------------------------------------
    eval_p = subparsers.add_parser('eval', parents=[common_parser, ev_parser])

    # -------------------------------------------
    # TESTEVAL
    # -------------------------------------------
    testeval_p = subparsers.add_parser('testeval', parents=[common_parser, tt_parser, ev_parser])
    # -------------------------------------------

    # -------------------------------------------
    # TRAINTESTEVAL
    # -------------------------------------------
    traintesteval_p = subparsers.add_parser('traintesteval', parents=[common_parser, tt_parser, ev_parser])

    global args
    args = main_parser.parse_args()

    argdict = vars(args)

    # -------------------------------------------
    # Read in the config file, if provided. Otherwise
    # the default config parser class will use the provided
    # defaults.
    # -------------------------------------------
    if args.config:
        if not os.path.exists(args.config):
            ERR_LOG.critical('The config file "{}" could not be found.'.format(args.config))
            sys.exit(2)
        else:
            alt_c = PathRelativeConfigParser()
            alt_c.read(args.config)
            for sec in alt_c.sections():
                for key in alt_c[sec].keys():
                    argdict[key] = alt_c[sec][key]

    # -------------------------------------------
    # Debug
    # -------------------------------------------
    if DEBUG_ON(args):
        os.makedirs(DEBUG_DIR(args), exist_ok=True)

    # -------------------------------------------
    # Load wordlist files for performance if testing or training
    # -------------------------------------------
    global en_wl, gls_wl, met_wl
    if args.subcommand in ['test', 'train', 'testeval', 'traintesteval']:
        en_wl = EN_WL(conf)
        gls_wl = GL_WL(conf)
        met_wl = MT_WL(conf)

    # -------------------------------------------
    # Load Gramlists
    # -------------------------------------------
    global gram_wl, gram_cased_wl, gram_list, gram_list_cased
    gram_wl = conf.get('files', 'gram_list', fallback=None)
    gram_cased_wl = conf.get('files', 'gram_list_cased', fallback=None)

    if gram_wl is None:
        ERR_LOG.warning("No gramlist file found.")
    if gram_cased_wl is None:
        ERR_LOG.warning("No cased gramlist file found.")

    def read_wl(path):
        grams = []
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        grams.append(line.strip())
        return grams

    gram_list = read_wl(gram_wl)
    gram_list_cased = read_wl(gram_cased_wl)

    if not gram_list:
        ERR_LOG.warning("No grams found.")
    if not gram_list_cased:
        ERR_LOG.warning("No cased grams found.")

    # -------------------------------------------
    # Set up the different filelists.
    # -------------------------------------------
    train_filelist = flatten(argdict.get('train_files', []))
    test_filelist = flatten(argdict.get('test_files', []))
    eval_filelist = flatten(argdict.get('eval_files', []))
    # -------------------------------------------

    def train(fl):
        if os.path.exists(args.classifier_path) and not args.overwrite_model:
            ERR_LOG.critical('Classifier model file "{}" exists, and overwrite not forced. Aborting training.'.format(args.classifier_path))
            sys.exit(2)
        cw = ClassifierWrapper()
        data = extract_feats(fl, cw, skip_noisy=True, **argdict)
        train_classifier(cw, data, **argdict)

    def test(fl):
        ERR_LOG.log(NORM_LEVEL, "Beginning classification...")
        classify_docs(fl, **vars(args))
        ERR_LOG.log(NORM_LEVEL, "Classification complete.")

    def eval(fl):
        ERR_LOG.log(NORM_LEVEL, "Beginning evaluation...")
        eval_files(fl, **vars(args))

    def testeval(fl):
        test(fl)
        classified_paths = [get_classified_path(p, getattr(args, 'classified_dir')) for p in fl]
        eval(classified_paths)

    def traintesteval(fl, ep):
        train(fl)
        testeval(ep)


    # Switch between the commands
    import cProfile
    if args.subcommand == 'train':
        if args.profile:
            cProfile.run('train(train_filelist)', 'train_stats')
        else:
            train(train_filelist)

    elif args.subcommand == 'test':
        if args.profile:
            cProfile.run('test(test_filelist)', 'test_stats')
        else:
            test(test_filelist)
    elif args.subcommand == 'eval':
        eval(eval_filelist)
    elif args.subcommand == 'testeval':
        testeval(train_filelist)
    elif args.subcommand == 'traintesteval':
        traintesteval(train_filelist, eval_filelist)