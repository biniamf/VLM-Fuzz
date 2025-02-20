# -- author: Biniam Fisseha Demissie
import subprocess
import argparse
import sys
import os
import re
import json
from openai import OpenAI
import random, string
import time
import logging
from collections import Counter, deque
import traceback 
import multiprocessing
from bcolors import bcolors
from manifest_parser import Parser
from ui_automator import *
from pre_ui_test import *
from _version import __version__

 
def disable_vkeyboard(adb_command):
    try:
        subprocess.run([f'{adb_command} shell settings put secure show_ime_with_hard_keyboard 0'],  shell = True, timeout=5)
    except subprocess.TimeoutExpired:
        print(f"[disable_vkeyboard] 5 seconds timeout reached")    
        
    
if __name__ == '__main__': 
    parser = argparse.ArgumentParser(description="Android UI Fuzzer")
    parser.add_argument('-a', '--apk', type=str, required=True, help='Path to APK file')
    parser.add_argument('-p', '--port', type=int, help='Emulator port')
    parser.add_argument('-b', '--budget', type=int, help='Budget in minutes')
    parser.add_argument('-v', '--version', action='version', version=f'Version: {__version__}')
    
    if len(sys.argv) == 1:
        print(f"Version: {__version__}")
        parser.print_help()
        sys.exit(1)
    
    args = parser.parse_args()
    args_dict = vars(args)

    # for debug
    # args_dict = {'apk': 'apks/com.angrydoughnuts.android.alarmclock_8_src.apk', 'port': None, 'budget': 60000}
    # args_dict = {'apk': '../apks/Book-Catalogue.apk', 'port': None, 'budget': 6000}
     
    apk = args_dict['apk'] 
    
    if args_dict['budget'] != None:
        total_budget = args_dict['budget']
    else:
        total_budget = 60 # default budget in mins

    emulator_port = None
    if args_dict['port'] != None:
        try:
            emulator_port = int(args_dict['port'])
        except:
            pass
            
    if emulator_port != None:
        adb_command = f"adb -s emulator-{emulator_port}"
    
    # adb root
    subprocess.run([f"{adb_command} root"],  shell = True)
    adb_root_out = subprocess.run([f"{adb_command} root"], shell = True, capture_output=True, text=True)
    print(f"[main] adb root: {adb_root_out.stdout}")
    
    # disable virtual keyboard
    disable_vkeyboard(adb_command)
    
    parser = Parser(apk)
    manifest = json.loads(parser.parse())
    package = manifest['package']
    components = manifest['components'][0]
    
    print(f"{bcolors.OKGREEN}[main] {components} {bcolors.ENDC}")
    
    system_broadcast = None
    if os.path.exists("system-broadcast.json"):
        with open("system-broadcast.json") as f:
            system_broadcast = json.load(f)

    output_root = "vlm-fuzz-output/"
    output_dir = output_root + package
    if not os.path.exists(output_root):
        os.mkdir(output_root)   
    
    if not os.path.exists(output_dir):
        os.mkdir(output_dir)   
        
    screenshot_dir = output_dir + "/screenshots"
    if not os.path.exists(screenshot_dir):
        os.mkdir(screenshot_dir)   
        
    vision_thoughts_dir = output_dir + "/screenshots_thoughts"
    if not os.path.exists(vision_thoughts_dir):
        os.mkdir(vision_thoughts_dir)   
            
    ptest = PreUITest(package, components, adb_command)
    try:
        computed_budget = ptest.inspect()
    except:
        # quick workaround for cases ZeroDivisionError error when app launch fails
        computed_budget = ptest.inspect()
    
    while True:
        print("[main] entering main loop")
        for service in components:
            if service['type'] != 'Service':
                continue
            
            uia = UIAutomator(package, adb_command)
            if not uia.start_service(service['name']):
                continue
            try:
                uia.analyze(where="start_analysis_service")
            except Exception as err:
                print(err)          
                traceback.print_exc()      
            visited.clear()   

        for receiver in components:
            
            if receiver['type'] != "Receiver":
                continue
            
            uia = UIAutomator(package, adb_command)        
            intent_filters = receiver['intent-filters'] 
            
            for filter in intent_filters:
                action = None
                action_dict = next((f for f in filter if 'action' in f), None)
                if action_dict:
                    action = action_dict['action']
                
                broadcast = None
                if system_broadcast is not None:
                    for broadc in system_broadcast:
                        if broadc['action'] == action:
                            broadcast = re.sub(f"{adb_command} shell am broadcast ", "", broadc['adb'][0])
                            broadcast = re.sub("com.example.app", package, broadcast)
                            break
                
                if not uia.send_broadcast(action, receiver['name'], broadcast):
                    time.sleep(2)                    
                    continue
            
            if len(intent_filters) == 0:
                if not uia.send_broadcast(None, receiver['name']):
                    time.sleep(2)                    
                    continue
            
        for activity in components:
            
            if activity['type'] != 'Activity':
                continue
            
            activity['name'] = activity['name'].replace(package, "")
            intent_filters = activity['intent-filters'] 
            
            # FIXME: "exported"="true" cases are missed
            if len(intent_filters) == 0:
                continue
                        
            _activity_name = activity['name'].replace(".", "")
            if _activity_name in computed_budget:
                current_component_budget = total_budget * computed_budget[_activity_name]['budget']
            else:
                current_component_budget = total_budget
                
            command = ""
            for filter in intent_filters:
                for item in filter:
                    if 'action' in item:
                        command = f" -a {item['action']} "
                    if 'category' in item:
                        command += f" -c {item['category']} "
                    # TODO: include data extras -> in v0.2

            ui_stack.clear()
            vision_ai_result = {}
            uia = UIAutomator(package, adb_command, vision_ai_result=vision_ai_result)
            try:
                if activity['name'] in vision_ai_result:
                    vision_ai_result.pop(activity['name'])
                
                # save the intent used to start this component in case we need to restart -> v0.2
                # uia.intent = random.choice(adb_intents) if len(adb_intents) > 0 else ""

                if not uia.start_app(activity['name'], command):
                    # if we were not able to start the component, try the next
                    continue
                
                p = multiprocessing.Process(target=uia.analyze)
                p.start()
                
                p.join(60*current_component_budget) # 60 * mins = seconds
                
                if p.is_alive():
                    print(f"{bcolors.FAIL}[main] budget of {current_component_budget} ran out for {_activity_name}... terminating{bcolors.ENDC}")
                    p.terminate()
                    p.join()
            except Exception as err:
                print(err)
                traceback.print_exc() 
                sys.exit(1)
            visited.clear()

