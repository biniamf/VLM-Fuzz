# -- author: Biniam Fisseha Demissie
from bcolors import bcolors
from ui_automator import UIAutomator

# FIXME: this test might fail if we have multiple emulators because adb_command is not visible here --> in UUIAutomator

class PreUITest:
    def __init__(self, package, components, adb_command):
        self.package = package
        self.components = components
        self.budget = dict() 
        self.adb_command = adb_command
        
    def count(self, view_items, public):
        items = {}
        unique_clz = set()
        for item in view_items:
            unique_clz.add(item.comp_class)
            if item.comp_class in items:
                items[item.comp_class] += 1
            else:
                items[item.comp_class] = 1                
                
        return {"count": len(view_items), "menu_count": 0, "public": public, "unique": len(unique_clz), "classes": items}
        
    
    def compute_budget_ratio(self):
        
        total_pub = 0
        total_uniq_pub = 0
        total_priv = 0
        total_uniq_priv = 0
        for item in self.budget.items():
            if item[1]['public']:
                total_pub += item[1]['count'] + (item[1]['menu_count'] * 4)
            else:
                total_priv += item[1]['count'] + (item[1]['menu_count'] * 4)
            
            if total_pub == 0:
                total_pub = 1        
            
            if item[1]['public']:
                total_uniq_pub += item[1]['unique'] + (item[1]['menu_count'] * 4)
            else:
                total_uniq_priv += item[1]['unique'] + (item[1]['menu_count'] * 4)

            if total_uniq_pub == 0:
                total_uniq_pub = 1                   
        
        for item in self.budget.items():
            # (item[1]['menu_count'] * 4) <= assuming each menu item will introduce at least 4 new items to interact with in the new UI
            if item[1]['menu_count'] > 0:
                if item[1]['public']:
                    item[1]['budget'] = (item[1]['count'] + item[1]['menu_count'] * 4)/total_pub  
                    item[1]['budget_unq'] = (item[1]['unique'] + item[1]['menu_count'])/total_uniq_pub
                else:
                    if total_priv > 0:                    
                        item[1]['budget'] = (item[1]['count'] + item[1]['menu_count'] * 4)/total_priv
                        item[1]['budget_unq'] = (item[1]['unique'] + item[1]['menu_count'])/total_uniq_priv
            else:
                if item[1]['public']:
                    item[1]['budget'] = item[1]['count']/total_pub
                    item[1]['budget_unq'] = item[1]['unique']/total_uniq_pub
                else:
                    if total_priv > 0:
                        item[1]['budget'] = item[1]['count']/total_priv  
                        item[1]['budget_unq'] = item[1]['unique']/total_uniq_priv
 

    def merge_count(self, focus, menu_items, public):
        
        if focus in self.budget.keys():
            _budget = self.budget.get(focus)
            
            items = {}
            unique_clz = set()
            for clz in _budget['classes']:
                unique_clz.add(clz)
                items[clz] = _budget['classes'][clz]
               
            for clz in menu_items['classes']:
                unique_clz.add(clz)
                if clz in items:
                    items[clz] += menu_items['classes'][clz]
                else:
                    items[clz] = menu_items['classes'][clz]                                        
    
            return {"count":  menu_items['count'] + _budget['count'], "public": public, "menu_count": menu_items['count'], "unique": len(unique_clz), "classes": items}
        
        # we should not reach here
        return None
    
    def inspect(self):
        
        for component in self.components:
            if component['type'] != 'Activity':
                continue
            
            if len(component['intent-filters']) > 0:
                public = True
            else:
                continue
            
            uia = UIAutomator(self.package, self.adb_command)
            
            try:
                _loop_counter = 0
                while _loop_counter < 5:
                    _loop_counter += 1
                    if not uia.start_app(component['name']):
                        continue
                    break
                
                if _loop_counter == 5:
                    print(f"{bcolors.WARNING}[pre_ui_test.inspect] could not start {component['name']} {bcolors.ENDC}")
                    continue
            except:
                continue
            
            # count widgets
            if  uia.check_ignore_list(uia.currentFocus):
                uia.update_view()
                uia.perform_tap_actions(uia.currentViewItems, pre_test=True)
                uia.setCurrentFocus()
            
            if uia.currentFocus in component['name']:             
                
                uia.update_view()
                self.budget.update({uia.currentFocus: self.count(uia.currentViewItems, public)})
                
                # check if clicking menu would show more
                uia.tap_menu_button()

                _popup, diff_count, count_changed = uia.check_items_count()
                if _popup:
                    uia.update_view()
                    menu_item_count = self.count(uia.currentViewItems, public)
                                
                    new_budget = self.merge_count(uia.currentFocus, menu_item_count, public)
                    
                    if new_budget:
                        self.budget.update({uia.currentFocus: new_budget})
            else:
                self.budget.update({uia.currentFocus: {"count": 0, "menu_count": 0, "unique": 0,"public": public, "classes": {}}})
                print(f"{bcolors.WARNING}[pre_ui_test.inspect] can't find UI for {component['name']} (found {uia.currentFocus}) {bcolors.ENDC}")
            # return [component, budget]
        
        # print(self.budget)
        self.compute_budget_ratio()     
        return self.budget