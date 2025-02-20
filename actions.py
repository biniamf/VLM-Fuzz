# -- author: Biniam Fisseha Demissie
from enum import Enum

class Action(Enum):
    TAP = 1
    SWIPE = 2 # LEFT <-- NOT YET: RIGHT, UP, DOWN
    SCROLL = 3 # is it = SWIPE UP/DOWN?
    TEXT = 4
    LONG_PRESS = 5
    ENTER = 6
    BACK = 7
    MENU = 8
    START = 10
    SCROLL_UP = 11
    SCROLL_LEFT = 12
    SCROLL_RIGHT = 13
    NONE = None