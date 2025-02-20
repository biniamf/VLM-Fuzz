# -- author: Biniam Fisseha Demissie
import subprocess
import os
import re
import json
import xmltodict
from openai import OpenAI
import random, string
import time
import logging
from collections import deque
import traceback 
import cv2
import pyshine as ps
import aivision
import prompt
from copy import copy
from bcolors import bcolors
from transition import TransitionRecord
from actions import Action        
from component import Component
from enum import Enum
from returns import RETURNS

output_root = "vlm-fuzz-output/"

open_ai_key =  os.getenv("OPENAI_API_KEY") 

vision_ai_result = {}

screenshot_dir = ""
# adb path
adb_command = "adb"
# not used but will hold the list of already processed items
expired = list()

# common pop up components to ignore [=> i.e., they might be related to the current app, handle them, do not just click back]
ignore_component_list = ["PopupWindow", "GrantPermissionsActivity", "DeprecatedTargetSdkVersionDialog", "Application Not Responding"]

# SCROLL DOWN
SCROLL_DOWN_DISTANCE = 800

SCROLL_UP_DISTANCE = 800
SCROLL_LEFT_DISTANCE = 500
SCROLL_RIGHT_DISTANCE = 500

arrow = 1 # for callgraph depth visualization A->B-->C--->D...
ui_stack = deque()
ui_class_stack = []
visited = []

class UIAutomator:

    component_hashes = None
    adb_command = ""

    def __init__(self, package, adb_command, vision_ai_result=None):
        self.flat_hierarchy = []
        self.currentFocus = ""
        self.currentViewItems = [] #set()
        self.package = package
        self.component_hashes = set()
        self.intent = ""
        self.transition = TransitionRecord()
        self.items_count = 0        
        self.screen_size = None
        self.currentViewSize = None
        self.isPopup = False
        self.screenshot_dir = ""
        self.vision_thoughts_dir = ""
        self.vision_action_summary = "I have done no actions so far."
        self.adb_command = adb_command
        
        if vision_ai_result:
            vision_ai_result = vision_ai_result
        
    def openai_api(self, system, user):
        client = OpenAI(api_key=open_ai_key)

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                "role": "system",
                "content": f"{system}"
                },
                {
                "role": "user",
                "content": f"{user}"
                }
            ],
            temperature=0.5,
            max_tokens=2048,
            top_p=0.9,
            frequency_penalty=0.3,
            presence_penalty=0
        )
        
        return response
   
    def flatten_hierarchy(self, flat_hierarchy, node):
        if isinstance(node, dict):

            f = {}
            for a, e, in node.items():
                
                if isinstance(e, dict) or isinstance(e, list):
                    self.flatten_hierarchy(flat_hierarchy, e)
                else:
                    f.update({a.replace("@",""):e})
            if 'class' in f and 'android.view' not in f['class'] \
                and 'Layout' not in f['class']:
                flat_hierarchy.append(f)

        elif isinstance(node, list):
            for e in node:
                
                if isinstance(e, list) or isinstance(e, dict):
                    self.flatten_hierarchy(flat_hierarchy, e)

    def dump_current_window(self):

        result_focus_window = subprocess.run([f'{self.adb_command} shell "dumpsys window windows | grep -E \'mCurrentFocus\'"'], shell = True, capture_output=True, text=True)

        result_focus_app = subprocess.run([f'{self.adb_command} shell "dumpsys window windows | grep -E \'mFocusedApp\'"'], shell = True, capture_output=True, text=True)

        try:

            focus_app_text = (re.split(r"\su[\d]\s", result_focus_app.stdout)[-1]).replace("}","")
    
            focus_window_text = (re.split(r"\su[\d]\s", result_focus_window.stdout)[-1]).replace("}","")
            
                    
            match_focus_app = re.findall(r"([\w.]+\/[\w.$]+)", focus_app_text)
            
            match_focus_window = re.findall(r"([\w.]+\/[\w.$]+)", focus_window_text)
            
            component_app = ""
            component_window = ""
            if match_focus_app:
                component_app = match_focus_app[0]
            else:
                component_app = focus_app_text
  
            if match_focus_window:
                component_window = match_focus_window[0]
            else:
                component_window = focus_window_text                

            if "mCurrentFocus=null" in component_window:
                _comp_window_focus = []
            else:
                _comp_window_focus = [component_window.strip()]
            # ret is list for legacy reason. FIXME: fix dependency to string
            return _comp_window_focus, [component_app.strip()]
        except:
            return "", ""


    def check_ignore_list(self, comp):
        print(f"[check_ignore_list] about to check {comp} if it's in the ignore list: {ignore_component_list}")
        for c in ignore_component_list:
            if c in comp:
                return True
            
        return False

    def get_current_comp(self):
        print(f"{bcolors.WARNING}[get_current_comp]  about to get the current component name{bcolors.ENDC}")

        # wait few seconds
        for _ in range(10):
            component_re, focus_app = self.dump_current_window()

            if len(component_re) > 0:
                break

            if len(focus_app) > 0:
                print(f"{bcolors.WARNING}[get_current_comp] self.dump_current_window() did not return focus window, using focus app instead:  component: {focus_app[0]}{bcolors.ENDC}")
                break

        if len(component_re) > 0 or len(focus_app) > 0:
            component = None
            # get current component info
            if len(component_re) > 0:
                component = component_re[0]
            
            if not component and len(focus_app) > 0:
                component = focus_app[0]
            
            if component != None and "Application Error: " in component:
                print(f"{bcolors.FAIL}[get_current_comp] component start failed for {self.currentFocus}... {bcolors.ENDC}")
                return "IGNORE_COMPONENT: " + component

            # if top window isn't part of AUT or it isn't a popup or android permission dilaog
            if (component != None and self.package not in component and not self.check_ignore_list(component)) or component is None:
                if len(focus_app) > 0:
                    component = focus_app[0]
                    
                    if self.package not in component and not self.check_ignore_list(component):
                        print("[get_current_comp] found IGNORE_COMPONENT: " + component)
                        return "IGNORE_COMPONENT: " + component

            
            # if focus app is from AUT but focus windows is different, probably a popup
            if len(focus_app) > 0:
                if self.package in focus_app[0] and component.replace(self.package, "") != focus_app[0].replace(self.package, ""):
                    component = "PopupWindow" 

            # avoid processing e.g., 'Application Not Responding: com.android.systemui'
            if not self.check_ignore_list(component):
                # a.b/.component cases
                _comp = component.split("/")
                # if it's package/comp format
                if (len(_comp) > 1):
                    component = _comp[1]
                    component = component.replace(self.package, "") 
                    
                component = component.lstrip(".")

            print("[get_current_comp] current component = ", component)
            return component
        else:
            print("[get_current_comp] No component focused")
            return None
        
    def after_scroll_coor_update(self, component):
        for item in self.currentViewItems:

            if item.comp_class == component.comp_class and item.content_desc == component.content_desc and \
                item.resource_id == component.resource_id and item.text == component.text:
                if item.clickable_bound != component.clickable_bound:
                    # update clickable bounds
                    item.clickable_bound = component.clickable_bound
    
    # mostly used to "fix" potential ui change because of visible soft keyboard
    def send_scroll_up(self, coordinates, replay=False):
        subprocess.run([f'{self.adb_command} shell input swipe {coordinates[0]} {coordinates[1]} {coordinates[0]} {coordinates[1] + SCROLL_UP_DISTANCE}'], shell = True, capture_output=True, text=True)
        

    def send_scroll_right(self, coordinates, replay=False):
        if not replay:
            self.transition.add(self.currentFocus, coordinates, Action.SCROLL_LEFT)

        subprocess.run([f'{self.adb_command} shell input swipe {coordinates[0]} {coordinates[1]} {coordinates[0] + SCROLL_RIGHT_DISTANCE} {coordinates[1]}'], shell = True, capture_output=True, text=True)
    
    def send_scroll_left(self, coordinates, replay=False):
        
        if not replay:
            self.transition.add(self.currentFocus, coordinates, Action.SCROLL_LEFT)
    
        subprocess.run([f'{self.adb_command} shell input swipe {coordinates[0]} {coordinates[1]} {coordinates[0] - SCROLL_LEFT_DISTANCE} {coordinates[1]}'], shell = True, capture_output=True, text=True)
        # time.sleep(1)
    
    def send_scroll_down(self, coordinates, replay=False):
        
        if not replay:
            self.transition.add(self.currentFocus, coordinates, Action.SCROLL)
       
        subprocess.run([f'{self.adb_command} shell input swipe {coordinates[0]} {coordinates[1]} {coordinates[0]} {coordinates[1] - SCROLL_DOWN_DISTANCE}'], shell = True, capture_output=True, text=True)
        # time.sleep(1)



    # check if current visible component is the starting component
    # call after tap or navigation actions
    def check_comp_change(self):
        print("[check_comp_change] checking if component changed because of the previous action")

        # wait few seconds
        current_comp = self.get_current_comp()
        
        if current_comp is None or "IGNORE_COMPONENT" in current_comp:
            return True
        
        # FIXME: the ui stack has components in a.b.C format
        _current_comp_with_package = current_comp

        current_comp = current_comp.replace(self.package, "") 
            
        current_comp = current_comp.lstrip(".")

        print("[check_comp_change]", self.currentFocus, " --> ", current_comp)
        # FIXME: it could be just a popup window (cancel/confirm/ok button for example)
        if self.currentFocus == current_comp:
            print("[check_comp_change]", "focus window did not change")
            return False
        # popup windows of spinners or something else
        elif self.check_ignore_list(str(current_comp)):
            self.currentFocus = current_comp
            print("[check_comp_change]", "focus window changed")
            return True    
        else:
            # FIXME: focus might have changed but we might be back where we were before
            
            # initially
            if self.currentFocus == None:
                print("[check_comp_change]", "currentFocus is None")
                self.currentFocus = current_comp
                return False
            # here, should check if we are coming back to previous component
            # A -> B -> A or... A -> B -> C -> A ??
            elif _current_comp_with_package in ui_stack:
                # if we just came back to the last window in the stack
                return False
            else:
                print("[check_comp_change]","we are in a new window")
                print(f"{bcolors.WARNING}[check_comp_change]",self.currentFocus, "->", _current_comp_with_package,f"{bcolors.ENDC}")
                return True  
    
    def reset_rotate_screen(self):
        print(f"[reset_rotate_screen] resetting screen rotation")
        subprocess.run([f"{self.adb_command} shell settings put system accelerometer_rotation 0"], shell = True, capture_output=True, text=True)
        time.sleep(1)        
        
        subprocess.run([f"{self.adb_command} shell content insert --uri content://settings/system --bind name:s:user_rotation --bind value:i:0"], shell = True, capture_output=True, text=True)
        time.sleep(1)
    
    def rotate_screen(self):
        print(f"[rotate_screen] rotating screen")
        subprocess.run([f"{self.adb_command} shell settings put system accelerometer_rotation 0"], shell = True, capture_output=True, text=True)
        time.sleep(1)
        
        subprocess.run([f"{self.adb_command} shell content insert --uri content://settings/system --bind name:s:user_rotation --bind value:i:1"], shell = True, capture_output=True, text=True)
        time.sleep(1)
        
    def home_screen(self):
        print(f"[home_screen] entering home screen")
        subprocess.run([f"{self.adb_command} shell input keyevent KEYCODE_HOME"], shell = True, capture_output=True, text=True)
        time.sleep(1)
        
    def restore_app(self):
        print(f"[restore_app] restoring app from app switch")
        result = subprocess.run([f"{self.adb_command} shell dumpsys activity recents | grep 'realActivity' | grep {self.package}"], shell = True, capture_output=True, text=True)

        if "realActivity" in result.stdout:
            activity = result.stdout.split("=")[-1]
            result = subprocess.run([f"{self.adb_command} shell am start --activity-single-top {activity}"], shell = True, capture_output=True, text=True)
        else:
            # for some reason we couldn't find the activity in dumpsys, try app switch
            print(f"[restore_app] dumpsys did not work, trying app switch to restore activity")
            result = subprocess.run([f"{self.adb_command} shell input keyevent KEYCODE_APP_SWITCH"], shell = True, capture_output=True, text=True)
            
            # TODO: replace this part with self.scree_size
            result = subprocess.run([f"{self.adb_command} shell wm size"], shell = True, capture_output=True, text=True)
            
            if "Physical size" in result.stdout:
                try:
                    size = result.stdout.split(":")[1]
                    x, y = map(int, size.strip().split("x"))  
                    
                    subprocess.run([f"{self.adb_command} shell input tap {x/2} {y/2}"], shell = True, capture_output=True, text=True)
                except:
                    pass

    def get_screen_size(self):
        print(f"[get_screen_size] getting physical device screen size")
        result = subprocess.run([f"{self.adb_command} shell wm size"], shell = True, capture_output=True, text=True)
        
        if "Physical size" in result.stdout:
            try:
                size = result.stdout.split(":")[1]
                x, y = map(int, size.strip().split("x"))                
                print(f"[get_screen_size] {result.stdout}")
                
                return x, y
            except:
                traceback.print_exc() 

        print(f"[get_screen_size] {result.stderr}")             

        return None

    # not used in v0.1
    def toggle_wifi(self):
        print(f"[toggle_wifi] toggling wifi")
        result = subprocess.run([f"{self.adb_command} shell dumpsys wifi | grep -E 'Wi-Fi is' | awk -F ' ' '{{print $3}}'"], shell = True, capture_output=True, text=True)
        
        if "enabled" in result.stdout:
            subprocess.run([f"{self.adb_command} shell svc wifi disable"], shell = True, capture_output=True, text=True)
        elif "disabled" in result.stdout:
            subprocess.run([f"{self.adb_command} shell svc wifi enable"], shell = True, capture_output=True, text=True)
        else:
            print(f"[toggle_wifi] cannot toggle wifi... can't determine current state")
    
    def is_soft_keyboard_visible(self):
        return False
        
    def hide_soft_keyboard(self):
        while self.is_soft_keyboard_visible():
            self.tap_back()
    
    def sent_battery_level(self, level=None):
        if level is None:
            level = random.randint(1,30)
        
        subprocess.run([f"{self.adb_command} shell dumpsys battery set level {level}"], shell = True, capture_output=True, text=True)

    def remaining_unprocessed_items(self, viewItems, actions=None):
        remaining_items = 0
        for item in viewItems:
            if actions == None:
                if not item.ignore and not item.processed == True:
                    remaining_items += 1                
            else:                
                if not item.ignore and item.action in actions and not item.processed == True:
                    remaining_items += 1
        return remaining_items

    def send_tap(self, coordinates, viewItems, replay=False):
 
        remaining_items = 0
        for item in viewItems:
            if not item.ignore and item.action == Action.TAP and not item.processed == True:
                remaining_items += 1
        if not replay:               
            if self.transition.find(self.currentFocus, coordinates, Action.TAP) and remaining_items > 0:
                return True, False
            else:
                print(f"[send_tap] remaining unprocessed items = {remaining_items}")
            
            self.transition.add(self.currentFocus, coordinates, Action.TAP)
        
        subprocess.run([f'{self.adb_command} shell input tap {coordinates[0]} {coordinates[1]}'], shell = True, capture_output=True, text=True)

        print(f"[send_tap] tapping at {coordinates}")
        if replay:
            # do not check
            return

        return False, self.check_comp_change()          

    def tap_menu_button(self, replay=False):
        if not replay:        
            self.transition.add(self.currentFocus, None, Action.MENU)
        
        subprocess.run([f'{self.adb_command} shell input keyevent 82'], shell = True, capture_output=True, text=True)
        time.sleep(1)
        
        if replay:
            # do not check
            return        
        
        return self.check_comp_change()      

    def send_long_press(self, coordinates, viewItems, replay=False):
        
        remaining_items = 0
        for item in viewItems:
            if not item.ignore and item.action == Action.TAP and not item.processed == True:
                remaining_items += 1
                
        if not replay:                
            if self.transition.find(self.currentFocus, coordinates, Action.TAP) and remaining_items > 0:
                return True, False
            else:
                print(f"[send_long_press] remaining unprocessed items = {remaining_items}")                    
            self.transition.add(self.currentFocus, coordinates, Action.LONG_PRESS)
        
        subprocess.run([f'{self.adb_command} shell input swipe {coordinates[0]} {coordinates[1]} {coordinates[0]} {coordinates[1]} 1000'], shell = True, capture_output=True, text=True)

        print(f"[send_long_press] long pressing at {coordinates}")
        if replay:
            # do not check
            return
        
        return False, self.check_comp_change()  

        
    def send_swipe_left(self, coordinates, replay=False):
        if not replay:        
            self.transition.add(self.currentFocus, coordinates, Action.SWIPE)
        
        subprocess.run([f'{self.adb_command} shell input tap {coordinates[0]} {coordinates[1]}'], shell = True, capture_output=True, text=True)
        
        subprocess.run([f'{self.adb_command} shell input swipe {coordinates[0]} {coordinates[1]} {coordinates[0]-100} {coordinates[1]}'], shell = True, capture_output=True, text=True)

        if replay:
            # do not check
            return
        
        return self.check_comp_change()
    
    def item_compare(self, _popup, new_view, old_view):
        
        similarity_count = 0
        
        view2 = copy(old_view)
        for item in new_view:
            clickable_bounds = self.get_bounds(item['bounds'])
            
            for component in view2:                
                if item['class'] == component.comp_class and \
                    item['content-desc'] == component.content_desc and \
                    item['enabled'] == str(component.enabled).lower() and \
                    item['resource-id'] == component.resource_id: # and clickable_bounds == component.clickable_bound and (item['text'] == component.text or item['text'] == component.ai_input) and \
                            similarity_count += 1
                            view2.remove(component)
                            break        
        
        if len(new_view) + len(view2) == similarity_count:
            # all items are the same
            return _popup, len(view2), False
        
        # otherwise somethings are different
        return _popup, len(view2), True
        
    def check_items_count(self, parentViewItems=None):
        print("[check_items_count] about to compare UI item counts")
        # dump ui
        if not self.get_current_ui_xml():
            print("[check_items_count] --- ERROR ----- could not find dumped xml for the current ui after 20 attempts")
            return False, False, False
            # raise Exception("[check_items_count] could not find dumped xml for the current ui after 20 attempts")
        path =f'{output_root}{self.package}/{self.currentFocus.replace("$", "")}.xml'
        with open(path) as xml_file:
            _popup = False
            
            data_dict = xmltodict.parse(xml_file.read())
            
            json_data = json.loads(json.dumps(data_dict))
            
            bounds = json_data['hierarchy']['node']['@bounds']
            self.currentViewSize = bounds.replace("][",",").replace("[", "").replace("]", "").split(",")
            
            if self.screen_size is None:
                self.screen_size = self.get_screen_size()
            
            if self.currentViewSize and self.screen_size: 
                x_diff0 = int(self.currentViewSize[0]) - 0  # e.g., [0,0] [1080,1920] - [0,10] [1080, 1850]
                y_diff0 = int(self.currentViewSize[1]) - 0
                
                x_diff1 = int(self.screen_size[0]) - int(self.currentViewSize[2]) # e.g., [0,0,1080,1920] - [0,0, 1080, 1850]
                y_diff1 = int(self.screen_size[1]) - int(self.currentViewSize[3])
                
                
                if y_diff1 > 150 or x_diff1 > 150 or x_diff0 > 150 or y_diff0 > 150:
                    _popup = True
            
            flat_hierarchy = []

            self.flatten_hierarchy(flat_hierarchy, json_data)

            if parentViewItems == None:
                return self.item_compare(_popup, flat_hierarchy, self.currentViewItems)
            else:
                return self.item_compare(_popup, flat_hierarchy, parentViewItems)
        
    def check_sent_text(self, target_item, text):
        # dump ui
        if not self.get_current_ui_xml():
            return False
        
        with open(f"{output_root}{self.package}/{self.currentFocus}.xml") as xml_file:
            data_dict = xmltodict.parse(xml_file.read())

            json_data = json.loads(json.dumps(data_dict))
            
            flat_hierarchy = []

            self.flatten_hierarchy(flat_hierarchy, json_data)

            for item in flat_hierarchy:
                coordinates = self.get_bounds(item['bounds']) 

                # print(target_item.clickable_bound, "==", coordinates, target_item.clickable_bound == coordinates)
                if target_item.clickable_bound == coordinates \
                    and target_item.comp_class == item['class'] \
                    and target_item.resource_id == item['resource-id'] :
                    # check if the item accepted our input
                    if text not in item['text']:
                        print("[check_sent_text] sent text not accepted: ", text, " != ", target_item.text)
                        return True
                        
        return False

    def tap_back(self, replay=False):
        print("[tap_back] tapping BACK") 
        
        subprocess.run([f'{self.adb_command} shell input keyevent 4'], shell = True, capture_output=True, text=True)    
        
    def tap_enter(self, replay=False):
        if not replay:
            self.transition.add(self.currentFocus, None, Action.ENTER)
        
        subprocess.run([f'{self.adb_command} shell input keyevent 66'], shell = True, capture_output=True, text=True)      

        if replay:
            # do not check
            return
        return self.check_comp_change()

    # inserting text when keyboard is open
    def send_text_keyboard(self, text):   
        subprocess.run([f'{self.adb_command} shell input keyevent KEYCODE_MOVE_END'], shell = True, capture_output=True, text=True)
        subprocess.run([f"{self.adb_command} shell input keyevent --longpress $(printf 'KEYCODE_DEL %.0s' {{1..25}})"], shell = True, capture_output=True, text=True)
    
        # 10% of the time, empty data
        if (random.randint(1,100) > 90):
            return
        
        split_text = list(text)
        
        for _letter in split_text:
          subprocess.run([f'{self.adb_command} shell input text \"{_letter.replace(" ", "%s")}\"'], shell = True, capture_output=True, text=True)  


    def send_text(self, coordinates, text, replay=False):

        if not replay:        
            self.transition.add(self.currentFocus, coordinates, Action.TEXT, input=text)
     
        if text is None:
            text = ''.join(random.choices(string.ascii_letters + string.digits, k=random.randint(4,8)))

        subprocess.run([f'{self.adb_command} shell input tap {coordinates[0]} {coordinates[1]}'], shell = True, capture_output=True, text=True)

        subprocess.run([f'{self.adb_command} shell input keyevent KEYCODE_MOVE_END'], shell = True, capture_output=True, text=True)

        subprocess.run([f"{self.adb_command} shell input keyevent --longpress $(printf 'KEYCODE_DEL %.0s' {{1..25}})"], shell = True, capture_output=True, text=True)
        
        split_text = list(text)
        for _letter in split_text:
          subprocess.run([f'{self.adb_command} shell input text \"{_letter.replace(" ", "%s")}\"'], shell = True, capture_output=True, text=True)  


    def openai_req(self, el, force_int=False):

        if force_int:
            int_input = "numeric"
        else:
            int_input = "numeric or text"

        prompt = """Your task is to provide a possible <<int_input>> input for the give Android UI element below presented as JSON. This item is on component called <<ui_component>>. Return a JSON with only an 'input' field with the generated input or null if no input is needed. You should try to infer the best input from the provided JSON. 
        
        Here is an example output: {"input": "John"} where "John" is the generated input. Do not provide instructions.
        Another output example is: {"input": "swipe left"} if you want to generate a swipe left action.
        
        Here is the JSON object representing the element:
        ```
        <<element>>
        ```
        
        Keep the value short.
        """
        print("[openai_req] ", el)
        prompt = re.sub("<<int_input>>", int_input, prompt)
        prompt = prompt.replace("<<element>>", el)
        prompt = prompt.replace("<<ui_component>>", self.currentFocus)

        gpt_result = self.openai_api("You are a Android UI tester. You will be given a JSON containing details of a give UI element. Your task is to respond to the question with only JSON file without description.", prompt)
        
        print(gpt_result.choices[0].message.content)
        
        return gpt_result.choices[0].message.content

    def has_navigated_away(self):
        _current_comp = self.get_current_comp()
        if _current_comp is None or "IGNORE_COMPONENT" in _current_comp:
            return True
        
        # check if the action made us naviagate to another view... if so, tap back and see if we're back
        if self.currentFocus not in _current_comp and not self.check_ignore_list(_current_comp):            
            return True
        return False
    
    def screenshot(self, outfile):
        # hide keyboard 
        self.hide_soft_keyboard()
        
        result = subprocess.run([f'{self.adb_command} exec-out screencap -p > {outfile}'], shell = True, capture_output=True, text=True)
        print(f"{bcolors.WARNING}[screenshot] stderr = '{result.stderr}'{bcolors.ENDC}")

    def label_screenshot(self, viewItems, inpath, outpath):
        
        screen = cv2.imread(inpath)

        bg_color = (225, 52, 235)
        text_color = (0, 0, 0) # Color rgb(252, 3, 44)) #red

        right=20
        bottom=20
        
        for item in viewItems:
            if (item.action == Action.TAP or item.action == Action.TEXT) and not item.ignore:
                coordinates = item.clickable_bound
                left = int(coordinates[0])
                top = int(coordinates[1])
            
                screen = ps.putBText(screen, str(item.label), text_offset_x=left + right, text_offset_y=top + bottom,
                                                vspace=10, hspace=10, font_scale=1, thickness=2, background_RGB=bg_color,
                                                text_RGB=text_color, alpha=0.4)
        
        cv2.imwrite(outpath, screen)

    def get_labels(self, viewItems):
        labels = ""
        tappables = ""
        editables = ""
        for item in viewItems:
            if item.action == Action.TEXT:
                editables += f"{item.label}, "
            
            if item.action == Action.TAP:
                tappables += f"{item.label}, "
            
        return f"Clickables elements: {tappables}, Editables elements: {editables}"

    def complete_ai_actions(self, viewItems, stop=False, popup=False, parentViewItems=None, menu=False):
        global vision_ai_result
        editable_item_count = 0
        navigation = None
        
        has_textedit = False
        for _item in viewItems:
            if _item.action == Action.TEXT:
                 has_textedit = True
                 break
        
        # FIXME: find a way to store popups
        rnd = 0
        # if we have textedit in the UI, 30 percent of the time, try new AI query
        if has_textedit:
            rnd= random.randint(1,100)
        
        if rnd > 70 or self.currentFocus not in vision_ai_result or popup: 
            rand_sufix = random.randint(1,10000)
            screenshot_path = f"{self.screenshot_dir}/{self.currentFocus}_screenshot_{rand_sufix}.png"
            labelled_screenshot_path = f"{self.screenshot_dir}/{self.currentFocus}_screenshot_labelled_{rand_sufix}.png"
            
            
            self.screenshot(screenshot_path)
            self.label_screenshot(viewItems, screenshot_path, labelled_screenshot_path)
            
            labels = self.get_labels(viewItems)
            
            order, summary, thought, observation, oai_response = aivision.get_ai_sequence(labelled_screenshot_path, prompt.prompt_steps_gpt, self.currentFocus, self.vision_action_summary, labels)
            
            self.vision_action_summary = summary
            
            print(f"{bcolors.WARNING}[perform_action_vision] new summary {self.vision_action_summary}{bcolors.ENDC}")
            
            if oai_response:
                with open(f"{output_root}{self.vision_thoughts_dir}/{self.currentFocus}_screenshot_labelled_{rand_sufix}_thoughts.txt", "w") as f:
                    # name of activity
                    f.write(f"Activity: {self.currentFocus}\n")
                    # list of ui items labelled
                    f.write(f"Labels: {labels}\n")
                    f.write("======== AI Response ========\n") 
                    f.write(oai_response)           
                    f.close()         
    
            # do not store popup related answers
            if not popup:
                vision_ai_result.update({self.currentFocus: order})
        else:
            order = vision_ai_result[self.currentFocus]
            
        if order == None:
            return RETURNS.FAIL

        inputs = order.replace("[","").replace("]","").split(";")
        
        
        if len(inputs) == 0:
            return RETURNS.FAIL

        replay_count = 0
        for action in inputs:
            
            action = action.strip()
            if self.has_navigated_away():
                self.tap_back()
                
                if self.has_navigated_away():
                    current_comp = self.get_current_comp()
                    
                    # avoid replaying a crashing component
                    current_unprocessed_items_count = self.remaining_unprocessed_items(self.currentViewItems, [Action.TEXT])
                    if current_unprocessed_items_count > 0 and not self.check_ignore_list(self.currentFocus) and replay_count < 1:
                        replay_count += 1
                        if not self.transition.replay(self.adb_command, self):
                            return False

                        # if we can't replay, give up
                        if self.has_navigated_away():
                            return False
                    else:
                        return False    

            if "text" in action:
                string = re.findall(r"text\((.*?)\)", action)
                if string:
                    # input box is already focus, keyboard is visible
                    self.send_text_keyboard(string[0])
                    
                    self.tap_enter()
                    
                    self.hide_soft_keyboard()
                    
                #inputs.remove(action)
                action = action.strip()
                editable_item_count += 1
                continue
                    
            elif "input" in action:
                match = re.findall(r'^input\((.*),[^"]+"([^"]+)"\)$', action)
                
                if match and len(match) > 0:
                    # (5, "John Doe")
                    label = int(match[0][0]) 
                    string = match[0][1]

                    for item in self.currentViewItems:
                        if item.label == label and item.action == Action.TEXT:
                            self.send_text(item.clickable_bound, string)
                            
                            item.processed = True
                            self.tap_enter()
                            # we have already found the item, we are done   
                            break
                # inputs.remove(action)
                editable_item_count += 1
                
    
            # for action in inputs:
            
            elif "tap" in action or "long_press" in action:
                tap_action = True
                if "tap" in action:
                    label = re.findall(r"tap\((.*?)\)", action)[0]    
                else:
                    tap_action = False # instead long press
                    label = int(re.findall(r"long_press\((.*?)\)", action)[0])      
                    
                if "ENTER" == label:
                    self.tap_enter()
                    continue

                if "BACK" == label:
                    # seems like we are done here
                    self.tap_back()
                    continue
            
                # by now it should tap(#)
                label = int(label)
                
                # NOTE: we should break if there's a UI change
                for item in self.currentViewItems:
                    if item.label == label and item.action == Action.TAP or item.action == Action.LONG_PRESS:                        
                        if (tap_action):
                            _ignored, tap_changed_ui = self.send_tap(item.clickable_bound, viewItems)
                        else:
                            _ignored, tap_changed_ui = self.send_long_press(item.clickable_bound, viewItems)

                        item.processed = True
                        # if the tap changed the UI
                        if (tap_changed_ui):
                            
                            uia = UIAutomator(self.package,  self.adb_command)

                            if "Spinner" in item.comp_class:
                                uia.analyze(spinner=True, where="ACTION.TAP | SPINNER", sub_transition=self.transition.copy(), parentViewItems=viewItems)
                            else:
                                uia.analyze(where="ACTION.TAP | UI CHANGE", sub_transition=self.transition.copy(), parentViewItems=viewItems)
                            
                        else:
                            if _ignored:
                                continue
                            
                            _popup, diff_count, count_changed = self.check_items_count()
                            if (count_changed):                                
                                # if we are in a popup, set parent as its previous parent/background
                                if popup:
                                    _parent = parentViewItems
                                else:
                                    _parent = viewItems # current view
                                    
                                if _popup and not self.check_ignore_list(self.currentFocus):
                                    uia = UIAutomator(self.package, self.adb_command)
                            
                                    uia.analyze(popup=True, where="ACTION.TAP | POPUP", parentViewItems=_parent, sub_transition=self.transition.copy())
                                else:
                                    uia = UIAutomator(self.package, self.adb_command)
                                    
                                    ret = uia.analyze(where="ACTION.TAP", parentViewItems=_parent, sub_transition=self.transition.copy(), not_skip=True)
                                    
                                    if ret == RETURNS.SUCCESS:
                                        # we have already analyzed the changed ui, don't bother continuing
                                        return True
                                    
                                    if self.isPopup and ret == RETURNS.CURRENT_VIEW_EXISTS:
                                        if self.check_popup():
                                            _popup, diff_count, count_changed = self.check_items_count(viewItems)
                                            if count_changed:
                                                return False
                                        else:    
                                            return False                                                         
      
            elif "scroll" in action:
                direction = int(re.findall(r"scroll\((.*?)\)", action)[0])  

                if self.screen_size is None:
                    self.screen_size = self.get_screen_size()  
                    
                coor = [self.screen_size[0]/2,self.screen_size[1]/2]                
                if direction == "UP":
                    self.send_scroll_up(coor)
                else:
                    self.send_scroll_down(coor)
                        
            elif "FINISH" in action:
                pass
                # self.tap_back()
                # return         
            else:
                print(f"[perform_action_vision] unknown action specified: {action}")
            

        # before doing menus, check if we have leftover items on the screen
        if not self.isPopup:
            processed_count = 0
            interactive_count = 0
            for _item in viewItems:
                if _item.ignore:
                    continue
                
                interactive_count += 1
                if _item.processed:
                    processed_count += 1
            
            left_over = interactive_count - processed_count
            # launch non AI version
            if left_over > 0:
                return RETURNS.FAIL

        if not self.perform_scroll_actions(viewItems, stop, popup, parentViewItems, menu):
            return RETURNS.FAIL
                
        if not self.perform_menu_tap_actions(viewItems, stop, popup, parentViewItems, menu):
            return RETURNS.FAIL              
        
        return RETURNS.SUCCESS #editable_item_count, navigation

    def get_num_menu_items(self):
        uia = UIAutomator(self.package, self.adb_command)       
        initial_count = uia.update_view()   
        
        self.tap_menu_button()
        
        _popup, changed_count, comp_changed = self.check_items_count()
        
        final_count = uia.update_view()   

        # tap menu again to close it in case it was open
        self.tap_menu_button()
        
        return comp_changed, final_count    
    
    # e.g., | Yes |  | No |  | OK |   <- probably will cause navigation
    def same_level_items(self, viewItems):        
        y2_coors = {}
        
        for item in viewItems:
            if item.action != Action.TAP and item.action != Action.LONG_PRESS:
                continue
            
            coor = item.bounds.replace("][",",").replace("[", "").replace("]", "").split(",") # [0, 0], [5,5]
            y2 = coor[3]
            
            if y2 in y2_coors:
                y2_coors[y2].append(item)
            else:
                y2_coors[y2] = [item]
            
        max_y2 = max(y2_coors, key=lambda y2: len(y2_coors[y2]), default=-1)

        if max_y2 == -1:
            return []
        else:
            # return the list of items with their Y2 coor equal
            return y2_coors[max_y2]

    # this can be handled by an LLM query
    def sort_sentiment(self, viewItems):
        
        negative_buttons = [ "cancel", "exit", "back", "return", "close", "decline", "abort", "discard", "reject", "undo", "delete", "remove", "quit", "abandon", "stop", "end", "dismiss", "ignore", "no", "deny", "opt out" ]
        positive_buttons = [ "Home", "Menu", "Done", "Finish", "Next", "Run", "Continue", "Skip", "Proceed", "Submit", "Save", "Apply", "OK", "Okay", "Confirm", "Accept", "Go", "Start", "Begin", "Create", "Add", "Update", "Send", "Share", "Login", "Sign Up", "Join", "Get Started", "Learn More", "Explore", "Search", "Find", "View", "Open", "Select", "Choose" ]
        positive = []
        neutral = []
        negative = []
        neutral_button = []
        positive_buttons = []
        negative_button = []
                
        for item in viewItems:
            if str(item.text).lower() in negative_buttons:
                if ".Button" in item.comp_class:
                    negative_button.append(item)
                else:
                    negative.append(item)
            elif str(item.text).lower() in positive_buttons:
                if ".Button" in item.comp_class:
                    positive_buttons.append(item)
                else:
                    positive.append(item)
            else:
                if ".Button" in item.comp_class:
                    neutral_button.append(item)
                else:
                    neutral.append(item)
        # buttons should be handled at the end        
        return neutral + neutral_button + positive + positive_buttons + negative + negative_button
    
    # this can be handled by an LLM query
    def sort_tappable_items(self, viewItems):
        
        viewItems2 = viewItems.copy()
        same_level_items = self.same_level_items(viewItems2)

        for item in same_level_items:
            viewItems2.remove(item)
            
        # sort also the same level items
        same_level_items = self.sort_sentiment(same_level_items)
            
        remaining_items = self.sort_sentiment(viewItems2)
        
        return remaining_items + same_level_items
    
    def perform_tap_actions(self, viewItems, stop=False, popup=False, parentViewItems=None, menu=False, pre_test=False, after_vision=False):
        # let's do tap/navigation actions after inserting inputs
    
        replay_count = 0
    
        # copy so that we can shuffle the list
        viewItems2 = viewItems.copy()
        
        random.shuffle(viewItems2)
        
        # sort negative button at the end
        viewItems2 = self.sort_tappable_items(viewItems2)

        for item in viewItems2:
            
            if item.processed:
                continue     

            # do not consider taps that might cause nagivations at the moment
            if not item.ignore and item.action == Action.TAP: # and not item.label in navigation:      
                _popup, diff_count, count_changed = self.check_items_count(viewItems2)                
                if self.has_navigated_away() or count_changed:                   
                    self.tap_back()
                    
                    _popup, diff_count, count_changed = self.check_items_count(viewItems2)    
                    if self.has_navigated_away() or count_changed:
                        current_comp = self.get_current_comp()
                        
                        # avoid replaying a crashing component
                        current_unprocessed_items_count = self.remaining_unprocessed_items(self.currentViewItems, [Action.TAP])
                        if current_unprocessed_items_count > 0 and not self.check_ignore_list(self.currentFocus) and replay_count < 1:
                            replay_count += 1
                            if not self.transition.replay(self.adb_command, self):
                                return False
                            
                            # if we can't replay, give up
                            if self.has_navigated_away():
                                return False

                        else:
                            return False                 
            
                # this version sends long_press randomly, v0.2 is based on xml dump
                if (random.randint(1,100) > 70):
                    _ignored, tap_changed_ui = self.send_long_press(item.clickable_bound, viewItems)
                else:
                    _ignored, tap_changed_ui = self.send_tap(item.clickable_bound, viewItems)

                item.processed = True
                if (tap_changed_ui):
                    
                    if pre_test:
                        return
                    
                    uia = UIAutomator(self.package, self.adb_command)

                    if "Spinner" in item.comp_class:
                        uia.analyze(spinner=True, where="ACTION.TAP | SPINNER", sub_transition=self.transition.copy(), parentViewItems=viewItems2, after_vision=after_vision)
                    else:
                        uia.analyze(where="ACTION.TAP | UI CHANGE", sub_transition=self.transition.copy(), parentViewItems=viewItems2, after_vision=after_vision)
                    
                else:
                    if _ignored:
                        continue
                    
                    _popup, diff_count, count_changed = self.check_items_count()
                    if (count_changed):                                

                        if popup:
                            _parent = parentViewItems
                        else:
                            _parent = viewItems2 # current view
                            
                        if _popup and not self.check_ignore_list(self.currentFocus) and not pre_test:
                            uia = UIAutomator(self.package, self.adb_command)
                                
                            uia.analyze(popup=True, where="ACTION.TAP | POPUP", parentViewItems=_parent, sub_transition=self.transition.copy(), after_vision=after_vision)
                        else:
                            if pre_test:
                                return
                                
                            uia = UIAutomator(self.package, self.adb_command)
                            
                            ret = uia.analyze(where="ACTION.TAP", parentViewItems=_parent, sub_transition=self.transition.copy(), not_skip=True, after_vision=after_vision)
                            
                            if ret == RETURNS.SUCCESS:
                                return True
                            
                            if self.isPopup and ret == RETURNS.CURRENT_VIEW_EXISTS:
                                if self.check_popup():
                                    _popup, diff_count, count_changed = self.check_items_count(viewItems2)
                                    if count_changed:
                                        return False
                                else:    
                                    return False

            if stop:
                return True
            
            if menu and not self.has_navigated_away():
                self.tap_menu_button()
        
        return True
                
    def perform_text__swipe_actions(self, viewItems, stop=False, popup=False, parentViewItems=None, menu=False, after_vision=False):
        
        replay_count = 0
        
        # we don't really expect these actions on a menu 
        if menu:
            return True
        
        editable_item_count = 0
        for item in viewItems:

            if item.action == Action.TAP or item.action == Action.SCROLL or item.processed:
                continue
                
            if self.has_navigated_away():
                self.tap_back()
                
                if self.has_navigated_away():
                    current_comp = self.get_current_comp()
                    
                    # avoid replaying a crashing component
                    current_unprocessed_items_count = self.remaining_unprocessed_items(self.currentViewItems, [Action.TEXT])
                    if current_unprocessed_items_count > 0 and not self.check_ignore_list(self.currentFocus) and replay_count < 1:
                        replay_count += 1
                        if not self.transition.replay(self.adb_command, self):
                            return False
                        
                        if self.has_navigated_away():
                            return False
                    else:
                        return False           
            
            # if it's a disabled item, ignore
            if item.ignore:
                continue


            expired.append(f"{self.currentFocus}.{item.comp_class}.{item.clickable_bound}")

            if not item.ignore and item.action == Action.TEXT:  
                editable_item_count += 1
                
                openai_json = self.openai_req(json.dumps(item.to_dict()))

                try:
                    oai_input = json.loads(openai_json)['input']
                except:
                    # just a "random" numeric val
                    oai_input = "12"
                item.set_ai_input(str(oai_input))

                ret = self.send_text(item.clickable_bound, str(str(oai_input)))

                item.processed = True
                
                if (ret != RETURNS.IGNORE and oai_input != "12" and not item.password and self.check_sent_text(item, str(oai_input))):
                    # input wasn't accepted, try numeric
                    openai_json = self.openai_req(json.dumps(item.to_dict()), True)

                    try:
                        oai_input = json.loads(openai_json)['input']
                    except:
                        oai_input = "12"

                    item.set_ai_input(str(oai_input))
                    
                    self.send_text(item.clickable_bound, str(oai_input))

            elif not item.ignore and item.action == Action.SWIPE or (item.ai_input is not None and "swipe left" in item.ai_input):
                    if (self.send_swipe_left(item.clickable_bound)):
                        uia = UIAutomator(self.package, self.adb_command)

                        uia.analyze(where="ACTION.SWIPE", sub_transition=self.transition.copy(), parentViewItems=viewItems, after_vision=after_vision)
                    
                    else:
                        _popup, diff_count, count_changed = self.check_items_count()
                        if count_changed:
                            if _popup and not self.check_ignore_list(self.currentFocus):

                                uia = UIAutomator(self.package, self.adb_command)
                                uia.analyze(popup=True, where="ACTION.SWIPE | POPUP", parentViewItems=viewItems, sub_transition=self.transition.copy(), after_vision=after_vision)
                            else:
                                uia = UIAutomator(self.package, self.adb_command)
                                uia.analyze(where="ACTION.SWIPE", parentViewItems=viewItems, sub_transition=self.transition.copy(), after_vision=after_vision)
                                                            

        # try tapping ENTER if needed
        if editable_item_count == 1:
            # self.tap_enter()
            if self.tap_enter():
                uia = UIAutomator(self.package, self.adb_command)

                uia.analyze(where="ACTION.ENTER | UI CHANGE", parentViewItems=viewItems, sub_transition=self.transition.copy(), after_vision=after_vision)
                
            else:
                _popup, diff_count, count_changed = self.check_items_count()
                if count_changed:
                    if _popup and not self.check_ignore_list(self.currentFocus):

                        uia = UIAutomator(self.package, self.adb_command)
                        uia.analyze(where="ACTION.ENTER", sub_transition=self.transition.copy(), parentViewItems=viewItems, after_vision=after_vision)
          
        return True                              
        
    def perform_scroll_actions(self, viewItems, stop=False, popup=False, parentViewItems=None, menu=False, after_vision=False): 
         
        replay_count = 0
        # we don't really expect this action on a menu 
        if menu:
            return True
                     
        for item in viewItems:
            if not item.ignore and item.action == Action.SCROLL:

                # FIXME: remove view items that are not visible anymore         
                item.processed = True
                
         
                if self.has_navigated_away():
                    self.tap_back()
                    
                    if self.has_navigated_away():
                        current_comp = self.get_current_comp()

                        current_unprocessed_items_count = self.remaining_unprocessed_items(self.currentViewItems, [Action.SCROLL]) 
                        if current_unprocessed_items_count > 0 and not self.check_ignore_list(self.currentFocus) and replay_count < 1:
                            replay_count += 1
                            if not self.transition.replay(self.adb_command, self):
                                return False

                            if self.has_navigated_away():
                                return False                            
                        else:
                            return False            

                if self.has_navigated_away():
                    return False
                
                self.send_scroll_down(item.clickable_bound)

                _popup, diff_count, count_changed = self.check_items_count()
                if count_changed:
                    if _popup and not self.check_ignore_list(self.currentFocus):

                        uia = UIAutomator(self.package, self.adb_command)
                        uia.analyze(where="ACTION.SCROLL | UI CHANGE", scroll=True, point=item.clickable_bound, sub_transition=self.transition.copy(), parentViewItems=viewItems, after_vision=after_vision)
                    else:
                        _popup, diff_count2, count_changed2 = self.check_items_count(parentViewItems)
                        if count_changed2:

                            uia = UIAutomator(self.package, self.adb_command)
                            uia.analyze(popup=False, where="ACTION.SCROLL | NEW ITEM", scroll=True, point=item.clickable_bound, sub_transition=self.transition.copy(), parentViewItems=viewItems, not_skip=True, after_vision=after_vision)    
                            
                            return True
                        else:
                            return False                        
                    
                else:
                    break       
                
        return True
        
    def perform_menu_tap_actions(self, viewItems, stop=False, popup=False, parentViewItems=None, menu=False, after_vision=False):        

        # we know already this ui has menu
        if menu:
            return True
        
        popup = False
        
        if self.has_navigated_away():
            self.tap_back()
            
            if self.has_navigated_away():
                current_comp = self.get_current_comp()

                if not self.transition.replay(self.adb_command, self):
                    return False
                
                if self.has_navigated_away():
                    return False             
        
        self.tap_menu_button()
            
        _popup, diff_count, count_changed = self.check_items_count()
        if count_changed:
            if not self.check_ignore_list(self.currentFocus):

                uia = UIAutomator(self.package, self.adb_command)
                uia.analyze(popup=True, menu=True, where="TAP MENU | POPUP", parentViewItems=viewItems, sub_transition=self.transition.copy(), after_vision=after_vision)
        
        __current_comp = self.get_current_comp()
        if __current_comp is not None and self.currentFocus not in __current_comp:
            if not self.transition.replay(self.adb_command, self):
                return False
        
        return True                

    def perform_action(self, viewItems, stop=False, popup=False, parentViewItems=None, menu=False, after_vision=False):

        viewItems_copy = viewItems.copy()
    
        rnd = random.randint(0,100)
        
        edit_text_count = 0
        for item in viewItems:
            if item.action == Action.TEXT:
                edit_text_count += 1
                
            if edit_text_count > 1:
                break
        
        if self.is_soft_keyboard_visible():
            self.hide_soft_keyboard()
        
        # consider it as a form
        if edit_text_count > 1:
            if not self.perform_text__swipe_actions(viewItems_copy, stop=stop, popup=popup, parentViewItems=parentViewItems, menu=menu, after_vision=after_vision):
                return RETURNS.FAIL
            if not self.perform_tap_actions(viewItems_copy, stop=stop, popup=popup, parentViewItems=parentViewItems, menu=menu, after_vision=after_vision):
                return RETURNS.FAIL    
            if not self.perform_scroll_actions(viewItems_copy, stop=stop, popup=popup, parentViewItems=parentViewItems, menu=menu, after_vision=after_vision):
                return RETURNS.FAIL
    
            if not self.perform_menu_tap_actions(viewItems_copy, stop=stop, popup=popup, parentViewItems=parentViewItems, menu=menu, after_vision=after_vision):
                return RETURNS.FAIL
        else:
            # maybe a search window            
            if not self.perform_text__swipe_actions(viewItems_copy, stop=stop, popup=popup, parentViewItems=parentViewItems, menu=menu, after_vision=after_vision):
                return RETURNS.FAIL
            if not self.perform_tap_actions(viewItems_copy, stop=stop, popup=popup, parentViewItems=parentViewItems, menu=menu, after_vision=after_vision):
                return RETURNS.FAIL
            if not self.perform_scroll_actions(viewItems_copy, stop=stop, popup=popup, parentViewItems=parentViewItems, menu=menu, after_vision=after_vision):
                return RETURNS.FAIL
            if not self.perform_menu_tap_actions(viewItems_copy, stop=stop, popup=popup, parentViewItems=parentViewItems, menu=menu, after_vision=after_vision):
                return RETURNS.FAIL
        
        if not self.has_navigated_away():    
            self.rotate_screen()
            self.reset_rotate_screen()
            
            self.home_screen()
            self.restore_app()


    # get the clickable area (center of item)
    def get_bounds(self, bounds):
        coor = bounds.replace("][",",").replace("[", "").replace("]", "").split(",")
        coordinates = list()
        coordinates.append((int(coor[0]) + int(coor[2]))/2)
        coordinates.append((int(coor[1]) + int(coor[3]))/2)

        return coordinates


    def start_app(self, activity, command="", replay=False):
        
        if activity is None:
            return False
        
        self.transition.head = None
        self.transition.add(activity, None, Action.START)   
        
        # can be global but the consider the multithreading
        self.screen_size = self.get_screen_size()
        
        try:
            # use back button below if killing is not an option
            subprocess.run([f'{self.adb_command} shell am force-stop {self.package}'],  shell = True)
 
            dot = ""
            # a.b.c/a.b.c.d
            if self.package in activity:
                activity = activity.replace(self.package, "")    
                if not activity.startswith("."):
                    dot = "."
                                    
            result = subprocess.run([f"{self.adb_command} shell am start -n {self.package}/{dot}{activity} {command}"], shell = True, capture_output=True, text=True)
                        
            if "Error type 3" in result.stderr:          
                # a.b.c/a.b.d.e                      
                if dot == "":
                    dot = "."
                else:
                    dot = ""
 
                result = subprocess.run([f"{self.adb_command} shell am start -n {self.package}/{dot}{activity} {command}"], shell = True, capture_output=True, text=True)
            
                # a.b.c/.d => .d
                _activity = activity.split(".")
                if len(_activity) == 1:
                    dot = "."
            
                if "Error type 3" in result.stderr:        
                    
                    if dot == "":
                        dot = "."
                        result = subprocess.run([f"{self.adb_command} shell am start -n {self.package}/{dot}{activity} {command}"], shell = True, capture_output=True, text=True)
                                    
            time.sleep(1)
 
            component = self.get_current_comp()
  
            if len(result.stderr.strip()) > 0:
                print(f"{bcolors.FAIL}[start_app] error: ", result.stderr, f"{bcolors.ENDC}")
            else:
                print("[start_app] start succeeded: ", result.stdout)
                
            # FIXME: consider splash screens
            if component:
                if component and not "IGNORE_COMPONENT" in component:
                    self.currentFocus = component
                    return True
                else:
                    self.currentFocus = None
                    return False                
            else:
                self.currentFocus = None
                return False
        except subprocess.TimeoutExpired:
            print(f"[start_app] 5 seconds timeout reached")
 
    def add_ui_element(self, view_copy, item, action, input, ignore, label, enabled, password=False, point=None):
        coordinates = self.get_bounds(item['bounds'])                                

        resource_id = ""  
        if "resource_id" in item:
            resource_id = item["resource_id"]

        if not self.screen_size:
            self.screen_size = self.get_screen_size()
        
        # if item lies above Y-SCROLL_DISTANCE, we have already seen it    
        if point and self.screen_size and coordinates[1] < (self.screen_size[1] - SCROLL_DOWN_DISTANCE):          
            component = Component(item["class"], resource_id , action, input, coordinates, item['content-desc'], item['resource-id'], item['text'], True, enabled, item['bounds'], password=password, label=label, processed=item['processed'])
        else:
            component = Component(item["class"], resource_id , action, input, coordinates, item['content-desc'], item['resource-id'], item['text'], ignore, enabled, item['bounds'], password=password, label=label, processed=item['processed'])

        component_copy = component.to_dict().copy()
        component_copy[0].pop('bounds')

        _hash = hash(str(component_copy))

        hlen = len(self.component_hashes)
        # try adding current component
        self.component_hashes.add(_hash)
        
        if view_copy is None:
            self.currentViewItems.append(component)
        else:
            view_copy.append(component)

    def dump_ui(self, path=None):
        
        # always hide keyboard before checking changes
        if self.is_soft_keyboard_visible():
            self.hide_soft_keyboard()
        
        if not self.currentFocus:
            self.currentFocus = self.setCurrentFocus()
            
        if not self.currentFocus:
            return False
        
        # sometime we might get "ERROR: null root node returned by UiTestAutomationBridge."
        # https://groups.google.com/g/android-testing-support-library/c/_yp04t8BC0M
        # solution for UIAutomator bug

        try:
            # get ui hierarchy
            result = subprocess.run([f'{self.adb_command} shell am broadcast -a io.uihierarchydumper.PERFORM_UI_HIERARCHY_DUMP io.uihierarchydumper'], shell = True, capture_output=True, text=True, timeout=5)
            time.sleep(2)
        except subprocess.TimeoutExpired:
            print(f"[dump_ui] 5 seconds timeout reached waiting")
            return False

        if path:
            _path = path
        else:
            _path = f'{output_root}{self.package}/{self.currentFocus.replace("$", "")}.xml'
        print("[dump_ui] dumping xml to local machine", self.package, "/", self.currentFocus)
        result = subprocess.run([f'{self.adb_command} pull /sdcard/window_dump.xml {_path}'], shell = True, capture_output=True, text=True)

        if os.path.exists(_path) and os.path.getsize(_path) > 0:
            subprocess.run([f'{self.adb_command} shell rm /sdcard/window_dump.xml'], shell = True, capture_output=True, text=True)            
            
            return True
        else:
            print(f"{bcolors.FAIL}[dump_ui] did not pull UI xml {result.stderr}{bcolors.ENDC}")
            return False
    

    def get_current_ui_xml(self, path=None):
        # sometimes pulling fails, so try again
        # (this was the case when using UIAutomator)
        for _ in range(20):
            if self.dump_ui(path):
                return True
            self.activate_window()

        # we will not try to start the dialogs/popups
        if self.currentFocus and not self.check_ignore_list(self.currentFocus):    # <========
            self.start_app(self.currentFocus, self.intent)
        else:
            return False

        
        return self.dump_ui(path)
    
    def activate_window(self):
        result = subprocess.run([f"{self.adb_command} shell input keyevent KEYCODE_APP_SWITCH"], shell = True, capture_output=True, text=True)
    
        if self.screen_size is None:
            self.screen_size = self.get_screen_size()    
    
        if self.screen_size:
            subprocess.run([f"{self.adb_command} shell input tap {self.screen_size[0]/2} {self.screen_size[1]/2}"], shell = True, capture_output=True, text=True)
    

    def check_popup(self):
        print(f"{bcolors.WARNING}[check_popup] checking UI is a popup {bcolors.ENDC}")

        if not self.screen_size:
            self.screen_size = self.get_screen_size()
        
        rnd = random.randint(1000,50000)
        path = f'{output_root}{self.package}/{self.currentFocus.replace("$", "")}_{rnd}_check_popup.xml'
        if not self.get_current_ui_xml(path=path):
            return RETURNS.FAIL

        with open(path) as xml_file:
            data_dict = xmltodict.parse(xml_file.read())

            json_data = json.loads(json.dumps(data_dict))
                        
            bounds = json_data['hierarchy']['node']['@bounds']
            current_view_size = bounds.replace("][",",").replace("[", "").replace("]", "").split(",")
            
            if self.currentViewSize and self.screen_size: 
                x_diff0 = int(current_view_size[0]) - 0  # e.g., [0,0] [1080,1920] - [0,10] [1080, 1850]
                y_diff0 = int(current_view_size[1]) - 0
                
                x_diff1 = int(self.screen_size[0]) - int(current_view_size[2]) # e.g., [0,0,1080,1920] - [0,0, 1080, 1850]
                y_diff1 = int(self.screen_size[1]) - int(current_view_size[3])
                
                if y_diff1 > 150 or x_diff1 > 150 or x_diff0 > 150 or y_diff0 > 150:
                    return True
                else: 
                    return False
        # couldn't do it
        return RETURNS.FAIL

    def update_view(self, view_copy = None, point=None, parentViewItems=None):
        
        if not self.screen_size:
            self.screen_size = self.get_screen_size()
        
        rnd = random.randint(1000,50000)
        path = f'{output_root}{self.package}/{self.currentFocus.replace("$", "")}_{rnd}.xml'

        if not self.get_current_ui_xml(path=path):
            return RETURNS.FAIL

        with open(path) as xml_file:
            data_dict = xmltodict.parse(xml_file.read())

            json_data = json.loads(json.dumps(data_dict))
                        
            bounds = json_data['hierarchy']['node']['@bounds']
            self.currentViewSize = bounds.replace("][",",").replace("[", "").replace("]", "").split(",")
            
            if self.currentViewSize and self.screen_size: 
                x_diff0 = int(self.currentViewSize[0]) - 0  # e.g., [0,0] [1080,1920] - [0,10] [1080, 1850]
                y_diff0 = int(self.currentViewSize[1]) - 0
                
                x_diff1 = int(self.screen_size[0]) - int(self.currentViewSize[2]) # e.g., [0,0,1080,1920] - [0,0, 1080, 1850]
                y_diff1 = int(self.screen_size[1]) - int(self.currentViewSize[3])
                
                if y_diff1 > 150 or x_diff1 > 150 or x_diff0 > 150 or y_diff0 > 150:
                    self.isPopup = True
                else: 
                    self.isPopup = False # default is false                   

            flat_hierarchy = []

            self.flatten_hierarchy(flat_hierarchy, json_data)

            self.items_count = len(flat_hierarchy)
            self.flat_hierarchy = flat_hierarchy

            count = 0
            tappable_items = 0
            
            self.currentViewItems = []
            
            for item in flat_hierarchy:
                count += 1
                try:
                    clazz = item['class'].split(".")[-1]
                    item['processed'] = False
                    
                    if parentViewItems:
                        for _item in parentViewItems:    
                            if _item.comp_class == item['class'] and \
                                    _item.content_desc == item['content-desc'] and \
                                    _item.resource_id == item['resource-id'] and \
                                    _item.text == item['text']: # for some cases where they don't change the ID
                                # by now it's the same item
                                if _item.processed == True:
                                    item['processed'] = True
                                    break
                    
                    # buttons and imageviews
                    # FIXME: long-clickable is add as a new state (v0.1 it is random) -> v0.2 
                    if (item['focusable'] == "true" or item['clickable'] == 'true') and ("Button" in clazz or "View" in clazz) and ("ScrollView" not in clazz and "ListView" not in clazz):
                        if item['enabled'] == 'true':
                            ig = False
                        else:
                            ig = True
                        self.add_ui_element(view_copy, item, Action.TAP, "", ignore=ig, label=count, point=point, enabled=item['enabled'])  
                        tappable_items += 1                           
                    # scrollable items: spinner, scrollview
                    elif "ScrollView" in clazz or ((item['focusable'] == "true" or item['clickable'] == 'true') and item['scrollable'] == 'true'):
                        self.add_ui_element(view_copy, item, Action.SCROLL, "", ignore=False, label=count, enabled=item['enabled']) 
                    # input text
                    elif item['focusable'] == "true" and "EditText" in clazz:

                        if item['enabled'] == 'true':
                            ig = False
                        else:
                            ig = True
                        
                        password = False
                        if item['password'] == "true":
                            password = True
                        self.add_ui_element(view_copy, item, Action.TEXT, "", ignore=ig, label=count, point=point, password=password, enabled=item['enabled'])   
                    elif item['focusable'] == "true" or item['clickable'] == 'true' or "Spinner" in clazz: 
                        if item['enabled'] == 'true':
                            ig = False
                        else:
                            ig = True
                        self.add_ui_element(view_copy, item, Action.TAP, "", ignore=ig, label=count, point=point, enabled=item['enabled']) 
                        tappable_items += 1 
                    elif len(item['text']) > 0 and len(item['resource-id']) > 0 and "TextView" in clazz:
                        # most likey used in menu or spinner as a button to tap
                        if item['enabled'] == 'true':
                            ig = False
                        else:
                            ig = True
                        self.add_ui_element(view_copy, item, Action.TAP, "", ignore=ig, label=count, point=point, enabled=item['enabled'])        
                        tappable_items += 1  
                    elif item['focusable'] == "false" and item['clickable'] == "false":
                        # WORKAROUND: add it to the list to avoid dynamic ui change but ignore them for action
                        self.add_ui_element(view_copy, item, Action.NONE, "", ignore=True, label=count, point=point, enabled=item['enabled'])      
                    else:
                        print(f"{bcolors.WARNING}[update_view] ignoring", item["class"], item['resource-id'], item['content-desc'], item['resource-id'], item['text'], "ignore = ",True, "enabled =", item['enabled'], "password =", password, "label =", count, f"{bcolors.ENDC}")

                except KeyError:
                    print(f"{bcolors.WARNING}Error on {clazz} --> {item['resource-id']} {item['focusable']} {item['clickable']} {bcolors.ENDC}")
                    print(KeyError)                    
                    pass
     
        for item in self.currentViewItems:
            if "ProgressBar" in item.comp_class:
                return RETURNS.PROGRESS_BAR
                        
        return RETURNS.SUCCESS 

    def setCurrentFocus(self):
        
        current_component = self.get_current_comp()
        
        # _comp = None
        if current_component is not None:        
            # a.b.Component cases
            # _comp = current_component.split(".")
            
            current_component = current_component.replace(self.package, "") 
                
            current_component = current_component.lstrip(".")   

            self.currentFocus = current_component
        else:
            self.currentFocus = None

    def analyze(self, spinner=False, popup=False, where="start_analysis_activity", scroll=False, parentViewItems=None, menu=False, point=None, sub_transition=None, not_skip=False, after_vision=False):
        global arrow, ui_stack

        if sub_transition:
            self.transition = sub_transition
    
        current_component = self.get_current_comp()
        
        if current_component is None:
            return RETURNS.FAIL 
        
        self.screenshot_dir = f"{output_root}{self.package}/screenshots/"
        self.vision_thoughts_dir = self.package + "/screenshots_thoughts"
                
        # TODO: this is already handled in get_current_comp() and would never be satisfied here, remove it?
        if "Application Error: " in current_component:
            print(f"{bcolors.FAIL}[analyze] component start failed for {self.currentFocus}... exiting {bcolors.ENDC}")
            self.tap_back()
            return RETURNS.FAIL 

        if where is not None:
            print(f"{bcolors.OKGREEN}[analyze] starting analysis for {current_component} from {where}{bcolors.ENDC}")

        if current_component is not None and "IGNORE_COMPONENT:" in current_component:
            print(f"{bcolors.WARNING}[analyze] exiting... found IGNORE_COMPONENT: {current_component}{bcolors.ENDC}")
            self.tap_back()
            return RETURNS.STOP             

        if current_component is not None \
            and (self.currentFocus is None \
            or len(self.currentFocus) == 0):       
            
            current_component = current_component.replace(self.package, "") 
                
            self.currentFocus = current_component.lstrip(".")    


        # this might be necessary to see if app stops working
        time.sleep(1)
        
        # update list of components in the view
        # check if we are still on the same ui before updating
        new_current_component = self.get_current_comp()
        if new_current_component != current_component:
            self.tap_back()
            
            new_current_component = self.get_current_comp()
            if new_current_component != current_component:
                # this is beyond pressing back... give up
                print(f"{bcolors.FAIL}[analyze] Away from {current_component} (on {new_current_component})... quitting{bcolors.ENDC}")
                return RETURNS.FAIL  

        try:
            view_update = self.update_view(point=point, parentViewItems=parentViewItems)
            
            if view_update == RETURNS.FAIL:
                print(f"{bcolors.FAIL}[analyze] View update for {current_component} failed... quitting{bcolors.ENDC}")
                return RETURNS.FAIL
            
            if view_update == RETURNS.PROGRESS_BAR:
                seconds_counter = 60 # wait max 1 min
                step = 2
                while seconds_counter > 0:
                    time.sleep(step)
                    view_update = self.update_view(point=point, parentViewItems=parentViewItems)
                    
                    if view_update != RETURNS.PROGRESS_BAR:
                        break
                    seconds_counter -= step
                
                # the loading page is stuck, give up
                if seconds_counter <= 0:
                    # try pressing back
                    subprocess.run([f'{self.adb_command} shell input keyevent 4'], shell = True, capture_output=True, text=True)
                    print(f"{bcolors.FAIL}[analyze] Stuck on a loading window  {current_component}{bcolors.ENDC}")
                    return RETURNS.STOP
                # current component might have changed
                current_component = self.get_current_comp()
        except:
            traceback.print_exc() 
            print(f"{bcolors.FAIL}[analyze] Could not update UI... exiting analysis  {current_component}{bcolors.ENDC}")
            return RETURNS.STOP 

        
        if (current_component in ui_stack) and not self.isPopup and not scroll:

            item_count_changed = False
            for u in ui_class_stack:
                if u['component'] == current_component:

                    similarity_count = 0
                    old_view = copy(u['class'].currentViewItems)
                    new_view = self.currentViewItems # make sure we have currentViewItems populated (call update_views() first)
                    for item in new_view:
                        # clickable_bounds = self.get_bounds(item['bounds'])
                        for component in old_view:                
                            # item.enalbed is for after click new UI items being eanbled
                            if item.comp_class == component.comp_class and \
                                item.content_desc == component.content_desc and \
                                item.enabled == str(component.enabled).lower() and \
                                item.resource_id == component.resource_id: # and clickable_bounds == component.clickable_bound and (item['text'] == component.text or item['text'] == component.ai_input) and \
                                    similarity_count += 1
                                    old_view.remove(component)
                                    break
                    # FIXME: why did we need the and not_skip part for popups
                    if (len(new_view) + len(old_view) == similarity_count): # and not not_skip: # not_skip= for scroll and popup cases, ignore item count change
                        print(f"{bcolors.WARNING}[analyze] Not analyzing this component, it's being analyzed:  {current_component}{bcolors.ENDC}")
                        print(f"{bcolors.WARNING}{ui_stack}{bcolors.ENDC}")
                        return RETURNS.CURRENT_VIEW_EXISTS
                    
                    # otherwise somethings are different
                    print(f"{bcolors.WARNING}[analyze] Analysis will continue, UI item count changed for  {current_component} ({len(new_view)} now vs {len(u['class'].currentViewItems)}){bcolors.ENDC}")
                    print(f"{bcolors.WARNING}{ui_stack}{bcolors.ENDC}")
                    item_count_changed = True
                    break


        #  or current_component in visited
        if visited.count(current_component) > 2 and not self.isPopup and not not_skip:
            subprocess.run([f'{self.adb_command} shell input keyevent 4'], shell = True, capture_output=True, text=True)
            print(f"{bcolors.FAIL}[analyze] exiting already tested 2 times  {current_component}{bcolors.ENDC}")
            return RETURNS.STOP 

        # update the old class with the current updated UI (most probably scrolled)
        if current_component in ui_stack and not self.isPopup:
            for c in ui_class_stack:
                if c['component'] == current_component:
                    ui_class_stack.remove(c)
                    ui_class_stack.append({'component':current_component,'class' :self})

        if current_component is not None and (current_component) not in ui_stack:
            arrow += 1
            ui_stack.append(current_component)
            ui_class_stack.append({'component':current_component,'class' :self})
        elif len(ui_stack) > 0 and ui_stack[-1] == current_component and not self.isPopup and not scroll and not item_count_changed:
            # we are probably coming back from another window (e.g, popup)
            print(f"{bcolors.OKGREEN}[analyze] {current_component} in ui stack and we are coming back{bcolors.ENDC}")
            return RETURNS.STOP # we probably won't reach here
        elif len(ui_stack) > 0 and current_component in ui_stack: # here, most likely a popup or because of scroll
            print(f"{bcolors.OKGREEN}[analyze] {current_component} in ui stack but we are continuing... {bcolors.FAIL}POPUP = {self.isPopup}, SCROLL = {scroll} {bcolors.ENDC}")
            # FIXME: we should return here since we already have this component in the stack
        # else: 
            # why are we here?
            # return

        if current_component is not None:
            
            # add in visited list before changing the format a.b.C format here
            if not self.isPopup and not scroll:
                visited.append(current_component)
                            
            current_component = current_component.replace(self.package, "") 
                
            current_component = current_component.lstrip(".")                 

            print("[analyze] BEFORE: self.currentFocus = ", self.currentFocus)
            self.currentFocus = current_component
            print("[analyze] NOW: self.currentFocus = ", self.currentFocus)                

            # This part ist just to print the activity lauch order graph A-->B--->C
            _arrow = "->"
            for _ in range(arrow):
                _arrow = "---" + _arrow
            __popup = ""
            if len(ui_stack) > 0:             
                if self.isPopup:
                    __popup = " [POPUP] "       
                if scroll:
                    __popup = " [SCROLL] "                                
                print(f"{bcolors.FAIL}[analyze] {_arrow}{ui_stack[-1]}{__popup}{bcolors.ENDC}")

            editables = False
            for _item in self.currentViewItems:
                if _item.action == Action.TEXT:
                    editables = True
                    break
                
            # if vision failed, ignore sub analysis for a moment
            ret = None
            if not menu and not after_vision and editables:
                ret = self.complete_ai_actions(self.currentViewItems, stop=spinner, popup=self.isPopup, parentViewItems=parentViewItems, menu=menu)
            
            if menu or (ret and ret == RETURNS.FAIL) or after_vision or not editables:
                _after_vision = False
                if ret and ret == RETURNS.FAIL:
                    _after_vision = True
  
                self.screenshot(self.screenshot_dir + current_component  + "_" + str(random.randint(1000,50000)) + "_" + ("POPUP_" if self.isPopup else "") + ".png")
                self.perform_action(self.currentViewItems, stop=spinner, popup=self.isPopup, parentViewItems=parentViewItems, menu=menu, after_vision=_after_vision)

            current_component = self.get_current_comp()
            if current_component is not None:
                    
                current_component = current_component.replace(self.package, "") 
                    
                current_component = current_component.lstrip(".")                       
                                
                if current_component == self.currentFocus and not (popup or scroll):
                    subprocess.run([f'{self.adb_command} shell input keyevent 4'], shell = True, capture_output=True, text=True)
                
            if len(ui_stack) > 0:

                if current_component == self.currentFocus and not (popup or scroll):
                    print(f"{bcolors.OKBLUE}[analyze] popping {ui_stack[-1]}{__popup}{bcolors.ENDC}")    
                    ui_stack.pop()
                    ui_class_stack.pop()
                    arrow -= 1

                    _arrow = "->"
                    for _ in range(arrow):
                        _arrow = "---" + _arrow

                    if len(ui_stack) > 0:
                        print(f"{bcolors.OKCYAN}{_arrow}[analyze]{ui_stack[-1]}{bcolors.ENDC}")
        else:
            return RETURNS.FAIL 
        
        return RETURNS.SUCCESS

    def send_broadcast(self, action, receiver, broadcast=None):
        receiver = receiver.replace(self.package, "")
        dot = ""
        if not receiver.startswith("."):
            dot = "."

        try:
            if broadcast is not None:
                subprocess.run([f"{self.adb_command} shell am broadcast {broadcast} {self.package}/{dot}{receiver}"], shell = True, capture_output=True, text=True, timeout=5)
            elif action is not None:
                subprocess.run([f"{self.adb_command} shell am broadcast -a {action} {self.package}/{dot}{receiver}"], shell = True, capture_output=True, text=True, timeout=5)
            else:
                subprocess.run([f"{self.adb_command} shell am broadcast {self.package}/{dot}{receiver}"], shell = True, capture_output=True, text=True, timeout=5)
        except subprocess.TimeoutExpired:
            print(f"[start_service] 5 seconds timeout reached")
            
    def start_service(self, service):
        service = service.replace(self.package, "")
        dot = ""
        if not service.startswith("."):
            dot = "."

        try:
            subprocess.run([f'{self.adb_command} shell am force-stop {self.package}'],  shell = True, timeout=5)

            subprocess.run([f"{self.adb_command} shell am startservice {self.package}/{dot}{service}"], shell = True, capture_output=True, text=True, timeout=5)
        except subprocess.TimeoutExpired:
            print(f"[start_service] 5 seconds timeout reached")