# -- author: Biniam Fisseha Demissie
from enum import Enum

class RETURNS(Enum):
    SUCCESS = 0
    FAIL = -1
    STOP = -2
    IGNORE = -3
    CURRENT_VIEW_EXISTS = -4
    PROGRESS_BAR = 1005
    UNKNOWN = None