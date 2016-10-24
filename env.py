import os
from configparser import ConfigParser, _UNSET, NoSectionError, NoOptionError

MY_DIR = os.path.dirname(__file__)
def absdir(path):
    return os.path.abspath(os.path.join(MY_DIR, path))

# -------------------------------------------
# Import the default config settings from defaults.ini
# -------------------------------------------
DEFAULTS = ConfigParser()
DEFAULTS_PATH = absdir('./defaults.ini')
DEFAULTS.read(DEFAULTS_PATH)

# -------------------------------------------
# Subclass the config parser to be able to obtain
# options from the default config
# -------------------------------------------
def setpaths(conf, path):
    secs = ['paths', 'files']
    for sec in secs:
        if sec in conf.sections():
            for opt in conf[sec]:
                v = conf[sec][opt]
                conf.set(sec, opt, os.path.abspath(os.path.join(os.path.dirname(path), v)))

setpaths(DEFAULTS, DEFAULTS_PATH)

class DefaultConfigParser(ConfigParser):

    def read(self, filenames, encoding=None):
        super().read(filenames, encoding=encoding)
        if isinstance(filenames, str):
            setpaths(self, filenames)

    def get(self, section, option, *args, **kwargs):
        try:
            v = super().get(section, option, *args, **kwargs)
        except NoSectionError as nse:
            v = None
        except NoOptionError as noe:
            v = None

        if v is None:
            return DEFAULTS.get(section, option, *args, **kwargs)
        else:
            return v

    def getboolean(self, section, option, *args, **kwargs):
        v = self.get(section, option, *args, **kwargs)
        if v in self.BOOLEAN_STATES:
            return self.BOOLEAN_STATES[v]
        else:
            raise ValueError("Not a boolean")

    def getfloat(self, section, option, *args, **kwargs):
        v = self.get(section, option, *args, **kwargs)
        return float(v)

    def getpath(self, sect, k):
        v = self.get(sect, k)
        return v


# Get the mallet directory...
def MALLET_DIR(config):
    return config.getpath('paths', 'mallet_dir')

def MALLET_BIN(config):
    return os.path.join(MALLET_DIR(config), 'bin/mallet')

def INFO_BIN(config):
    return os.path.join(MALLET_DIR(config), 'bin/classifier2info')

def JAVA_CP(config):
    return '{}:{}'.format(os.path.join(MALLET_DIR(config), 'class'),
                          os.path.join(MALLET_DIR(config), 'lib/mallet-deps.jar'))

def JAVA_ARGS(config):
    return ['java', '-Xmx' + config.get('runtime', 'java_mem'), '-ea',
            '-Djava.awt.headless=true',
            '-Dfile.encoding=UTF-8',
            '-server',
            '-classpath', JAVA_CP(config)]


# -------------------------------------------
# The following options are concerned with various
# folders for holding temporary and debug files
# are.
# -------------------------------------------

# The directory in which to place the human readable feature files.
def FEAT_DIR(config):
    return config.getpath('paths', 'feat_dir')

# Directory to the gold standard data for evaluation.
def GOLD_DIR(config):
    return config.getpath('paths', 'gold_dir')

# Directory in which to place output classified files
def OUT_DIR(config):
    return config.getpath('paths', 'out_dir')

# Whether or not to output debugging information
def DEBUG_ON(config):
    return config.getboolean('runtime', 'debug_on')

# The directory in which to store the information about the classifier feature
# weights, and raw labels
def DEBUG_DIR(config):
    return config.getpath('paths', 'debug_dir')


# -------------------------------------------
# Path to various text files
# -------------------------------------------
# Large English language wordlist.
def EN_WORDLIST(config):
    return config.getpath('files', 'en_wordlist')

# List of gloss-line words extracted from ODIN-2.
# 1
def GLS_WORDLIST(config):
    return config.getpath('files', 'gls_wordlist')

# List of meta line words extracted from ODIN-2.1
def MET_WORDLIST(config):
    return config.getpath('files', 'met_wordlist')

# List of language names
def LNG_NAMES(config):
    return config.getpath('files', 'lng_names')

def HIGH_OOV_THRESH(config):
    return config.getfloat('thresholds', 'high_oov')

def MED_OOV_THRESH(config):
    return config.getfloat('thresholds', 'med_oov')

# -------------------------------------------
# Load the Wordlist if it is defined in the config.
# -------------------------------------------
class WordlistFile(set):
    def __init__(self, path):
        super().__init__()
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    self.add(line.split()[0])

def WLF(config, f):
    return WordlistFile(f(config)) if os.path.exists(f(config)) else None

def EN_WL(config): return WLF(config, EN_WORDLIST)
def GL_WL(config): return WLF(config, GLS_WORDLIST)
def MT_WL(config): return WLF(config, MET_WORDLIST)


# =============================================================================
# List of grams, to be used
# =============================================================================

# These grams will be searched for case-insensitive.
GRAM_LIST = ['1SG', '1PL', '1SM',
             '2SG', '2P', '2SM',
             '3SG', '3REFL', '3SGP', '3SM', '3P']

# These grams will be searched for case sensitive.
CASED_GRAM_LIST = ['POSS',
                   'ACC','NOM', 'DAT', 'ERG', 'AOR', 'ABS', 'OBL', 'DUAL', 'REFL',
                   'NEG', 'TOP',
                   'FUT', 'PROG', 'PRES', 'PASS']


def USE_BI_LABELS(config):
    return config.getboolean('labels', 'use_bi_labels')


# Some lines appear as combinations of labels, such as "L-G-T" for all
# three on a single line. If this is set to true, these types of
# combined labels are allowed. If set to false, only the first
# of the multiple labels will be used.
def USE_MULTI_LABELS(config):
    return config.getboolean('labels', 'use_multi_labels')

# "Flags" are additional information that is intended to be included in
# the information about the line, such as +AC (for Author Citation)
# or +LN (for Language Name). These are stripped out by default, as
# otherwise they would result in an explosion of labels.
def STRIP_FLAGS(config):
    return config.getboolean('labels', 'strip_flags')

# =============================================================================
# Feature selection.
#
# In this section, various features are defined and can be enabled or
# disabled by the user. Read the comments, as some definitions are constants
# and should not be edited.
# =============================================================================


# -------------------------------------------
# High-level features.
#
# Set these to True or False, depending
# on whether you want that feature set enabled
# or not.
# -------------------------------------------

# Use the freki-block based features
FREKI_FEATS_ENABLED = True

# Use the text-based features
TEXT_FEATS_ENABLED  = True

# -------------------------------------------
# These three features control whether the
# features are included for the previous line,
# the line before that (prev_prev), or the next
# line.
# -------------------------------------------
def USE_PREV_LINE(config):
    return config.getboolean('featuresets', 'use_prev_line')

def USE_PREV_PREV_LINE(config):
    return config.getboolean('featuresets', 'use_prev_prev_line')

def USE_NEXT_LINE(config):
    return config.getboolean('featuresets', 'use_next_line')

# -------------------------------------------
# FEATURE CONSTANTS
#
# Associating a variable with the text string used in the config file.
# -------------------------------------------
F_IS_INDENTED = 'is_indented'
F_IS_FIRST_PAGE = 'is_first_page'
F_PREV_LINE_SAME_BLOCK = 'prev_line_same_block'
F_NEXT_LINE_SAME_BLOCK = 'next_line_same_block'
F_HAS_NONSTANDARD_FONT = 'has_nonstandard_font'
F_HAS_SMALLER_FONT = 'has_smaller_font'
F_HAS_LARGER_FONT  = 'has_larger_font'

# List of all the above
F_LIST = [F_IS_INDENTED, F_IS_FIRST_PAGE, F_PREV_LINE_SAME_BLOCK, F_NEXT_LINE_SAME_BLOCK, F_HAS_NONSTANDARD_FONT, F_HAS_SMALLER_FONT, F_HAS_LARGER_FONT]

T_BASIC = 'words'
T_HAS_LANGNAME = 'has_langname'
T_HAS_GRAMS = 'has_grams'
T_HAS_PARENTHETICAL = 'has_parenthetical'
T_HAS_CITATION = 'has_citation'
T_HAS_ASTERISK = 'has_asterisk'
T_HAS_UNDERSCORE = 'has_underscore'
T_HAS_BRACKETING = 'has_bracketing'
T_HAS_QUOTATION = 'has_quotation'
T_HAS_NUMBERING = 'has_numbering'
T_HAS_LEADING_WHITESPACE = 'has_leading_whitespace'
T_HIGH_OOV_RATE = 'high_oov_rate'
T_MED_OOV_RATE = 'med_oov_rate'
T_HIGH_GLS_OOV_RATE = 'high_gls_oov'
T_HIGH_MET_OOV_RATE = 'high_met_oov'
T_MED_GLS_OOV_RATE = 'med_gls_oov'
T_HAS_JPN = 'has_jpn'
T_HAS_GRK = 'has_grk'
T_HAS_KOR = 'has_kor'
T_HAS_CYR = 'has_cyr'
T_HAS_ACC = 'has_acc_lat'
T_HAS_DIA = 'has_dia'
T_HAS_UNI = 'has_uni'
T_HAS_YEAR = 'has_year'

T_LIST = [T_BASIC, T_HAS_LANGNAME, T_HAS_GRAMS, T_HAS_PARENTHETICAL, T_HAS_CITATION, T_HAS_ASTERISK, T_HAS_UNDERSCORE, T_HAS_BRACKETING,
          T_HAS_QUOTATION, T_HAS_NUMBERING, T_HAS_LEADING_WHITESPACE, T_HIGH_OOV_RATE, T_MED_OOV_RATE, T_HIGH_GLS_OOV_RATE, T_MED_GLS_OOV_RATE,
          T_HAS_JPN, T_HAS_GRK, T_HAS_KOR, T_HAS_CYR, T_HAS_ACC, T_HAS_DIA, T_HAS_UNI, T_HAS_YEAR]

# =============================================================================
# EDIT THIS SECTION
# =============================================================================

# -------------------------------------------
# Now, to enable/disable a particular feature,
# just comment out the line the feature is
# contained on.
# -------------------------------------------

def enabled_feats(config: DefaultConfigParser, section, featlist):
    enabled = set([])
    for feat in featlist:
        b = config.getboolean(section, feat)
        if b:
            enabled.add(feat)
    return enabled

def ENABLED_FREKI_FEATS(config: DefaultConfigParser):
    return enabled_feats(config, 'freki_features', F_LIST)

def ENABLED_TEXT_FEATS(config: DefaultConfigParser):
    return enabled_feats(config, 'text_features', T_LIST)


# =============================================================================
# Regular Expressions
#
# These are multiline expressions that were initially used for IGT detection.
#
# These are currently unused, but could be included to fire for lines which
# find themselves contained in such a regex.
# =============================================================================

REGEXES = '''
\s*(\()\d*\).*\n
\s*.*\n
\s*\[`'"].*\n
~
\s*(\()\d*\)\s\w\..*\n
\s*.*\n
\s\[`'"].*\n
~
\s*(\(\d)*\)\s*\(.*\n
\s.*\n
\s*.*\n
\s\[`'"].*\n
~
\s*(\()\d*\).*\n
\s*\w\..*\n
\s*.*\n
\s\[`'"].*\n
~
\s*\w.\s*.*\n
\s*.*\n
\s*\[`'"].*\n
~
\s*\w\)\s*.*\n
\s*.*\n
\s*\[`'"].*\n
~
\s*(\()\w*\).*\n
\s*.*\n
\s*\[`'"].*\n
~
//added 02-03-2005
\s*\d.*.*\n
\s*.*\n
\s*\[`'"].*\n
~
\s*(\()\d*\).*\n
.*\n
\s*.*\n
\s*\[`'"].*\n
~'''