# -- author: Biniam Fisseha Demissie
from actions import Action      
from bcolors import bcolors
from copy import copy
import subprocess
import time

class Event:
    def __init__(self, component, item_coor, action, input=None):
        self.component = component.lstrip(".")  
        self.item_coor = item_coor
        self.action = action
        self.input= input
        self.count = 0
        self.next = None
        self.prev = None
        
class TransitionRecord:    
    def __init__(self):
        self.head = None
    
    def add(self, component, item_coor, action, input=None):
        if component is None:
            return
        event = Event(component, item_coor, action, input)
        
        if self.head is None:
            self.head = event
            return
        
        current = self.head
        while (current.next): 
            current = current.next
        
        current.next = event  # ->
        event.prev = current  # <-

    def find(self, component, item_coor, action, input=None):
        event = copy(self.head)
        
        while (event):
            # TODO: decide whether event.input == input should be considered or not
            if event.component == component and event.item_coor == item_coor and event.action == action and event.input == input:
                print(f"{bcolors.WARNING} Event already sent in the current instance: ", event.component, event.item_coor, event.action, event.input,  f"{bcolors.ENDC}", sep=" -> " )
                return True
            
            event = event.next
        return False
    
    def copy(self):
        return copy(self)

    def sub_transition(self, currentFocus, uia):
        event = copy(self.head)
        
        while (event.next):
            event = event.next
        

        while (event.prev):
            if not uia.check_ignore_list(event.component):
                if event.prev.component != currentFocus:
                    break
            # stop if two subsequent taps
            if (event.action == Action.TAP or event.action == Action.LONG_PRESS) and \
                (event.prev.action == Action.TAP or event.prev.action == Action.LONG_PRESS): 
                    break
            event = event.prev
            
        if event:
            return copy(event)
        else:
            return None
            
    def replay(self, adb_command, uia):        
        # used to restore as start_app could update currentFocus
        currentFocus = uia.currentFocus
        
        if uia.check_ignore_list(currentFocus):
            print(f"{bcolors.WARNING} {currentFocus} in ignore list... Do not know how to arrive here. Not replaying...{bcolors.ENDC}")
            return False
                
        event = self.sub_transition(currentFocus, uia)                        
        
        if event is None:
            return False


        
        # try to the kill app anyway
        subprocess.run([f'{adb_command} shell input keyevent KEYCODE_HOME'],  shell = True)
        subprocess.run([f'{adb_command} shell am force-stop {uia.package}'],  shell = True)
        
        if not uia.start_app(currentFocus, replay=True):
            print(f"{bcolors.FAIL}[TransitionRecord.replay] starting component failed {bcolors.ENDC}" )
            return False
                

        _current_comp = None
        
        while (event):

        
            # let's not replay an event more than 6 time 
            # assume max 6 unprocessed items on the screen that made us replay
            if event.count > 6:
                event = event.next                                            
                continue
            
            if event.action == Action.START:
                if not uia.start_app(event.component, replay=True):
                    return False
            
            elif event.action == Action.TEXT:
                if _current_comp == event.component:
                    uia.send_text(event.item_coor, event.input, replay=True)
                
            elif event.action == Action.TAP:
                if _current_comp == event.component:
                    uia.send_tap(event.item_coor, uia.currentViewItems, replay=True) 
                
            elif event.action == Action.ENTER:
                if _current_comp == event.component:
                    uia.tap_enter(replay=True)
             
                            
            elif event.action == Action.SCROLL:
                if _current_comp == event.component:
                    uia.send_scroll_down(event.item_coor, replay=True)

            elif event.action == Action.SWIPE:
                if _current_comp == event.component:                
                    uia.send_swipe_left(event.item_coor, replay=True)         
                
            elif event.action == Action.LONG_PRESS:
                if _current_comp == event.component:                
                    uia.send_long_press(event.item_coor, uia.currentViewItems, replay=True)                                                
                
            elif event.action == Action.MENU:
                if _current_comp == event.component:
                    uia.tap_menu_button(replay=True)                                          
                        
            else:
                print(f"{bcolors.WARNING}[TransitionRecord.replay] Action not implemented for ", event.component, event.item_coor, event.action, event.input,  f"{bcolors.ENDC}", sep=", " )                                                
            
            _current_comp = uia.get_current_comp()
            if _current_comp is not None and currentFocus in _current_comp:
                uia.currentFocus = currentFocus
                # we have already reached our target UI, shall we stop?
                # DO NOT BREAK HERE unless you check if there are necessary interactions
       
            # uia.currentFocus = currentFocus
            
            event.count += 1
            
            event = event.next
            
            # ignore the last action that probably caused the UI change
            if event and event.next == None:
                break
                
        # self.head = None
        return True