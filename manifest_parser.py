# -- author: Biniam Fisseha Demissie
import re
import subprocess
import json
import traceback 

class Parser():
    manifest = ""
    package = ""
    def __init__(self, apk_path):
        
        result = subprocess.run([f'aapt dump xmltree {apk_path} AndroidManifest.xml'], shell = True, capture_output=True, text=True)

        if result.returncode == 0:
            self.manifest = result.stdout
            self.package = re.findall(r'package="([^"]+)"', self.manifest)[0]
        else:
            raise Exception(f"Could not dump AndroidManifest.xml for {apk_path}")

    def get_components(self):
        lines = self.manifest.splitlines()

        skip = True
        depth = 0

        for l in lines:
   
            
            if "uses-permission" in l:
                yield l
            


            __depth = l.find("N: ") + l.find("E: ") + l.find("A: ") + 2

            if __depth < depth:
                break

            if l.strip().startswith("E"):
                if "activity" in l \
                    or "receiver" in l \
                    or "service" in l \
                    or "action" in l \
                    or "data" in l \
                    or "intent-filter" in l \
                    or "category" in l:
                    
                    yield l

            if l.strip().startswith("A"):
                if "android:name" in l or "package" in l  or "scheme" in l or "pathPattern" in l or "host" in l:
                    yield l

    def get_x(self, x, line):
        if f"android:{x}" in line:
            name = re.findall(r'android:' + x + r'[^"]+"([^"]+)"', line)
            return name[0] if len(name) > 0 else ""
        
    def visit_action(self, gen):
        line = next(gen)
        name = self.get_x("name",line)
        try:
            line = next(gen)
        except StopIteration:
            line = None
        return line,{"action": name}

    def visit_category(self, gen):
        line = next(gen)
        name = self.get_x("name", line)
        
        try:
            line = next(gen)
        except StopIteration:
            line = None
        
        return line, {"category": name}
    
    def visit_data(self, gen):
        host = ""
        pattern = []
        scheme = ""
        try:
            while True:
                line = next(gen)
                
                if "E: " in line and "E: data" not in line:
                    break
                
                if "scheme" in line:
                    scheme = self.get_x("scheme", line)
                if "pathPattern" in line:
                    pattern.append(self.get_x("pathPattern", line))                                        
                if "host" in line:
                    host = self.get_x("host", line)            
        except StopIteration:
                pass                   

        return line, {"data": {"scheme": scheme, "pathPattern": pattern, "host": host}}

    def visit_intent_filter(self, component, name, line, gen):
        component = dict([("name", name),("type", component), ("intent-filters", [])])
        try:
            filter = []
            while True:
                
                if not line:
                    return None, component
                
                if "E: action" in line:
                    line, action = self.visit_action(gen)
                    filter.append(action)
                    continue

                if "E: category" in line:
                    line, category = self.visit_category(gen)
                    filter.append(category)
                    continue

                if "E: data" in line:
                    line, data = self.visit_data(gen)
                    filter.append(data)
                    continue

                if len(filter) > 0:
                    component["intent-filters"].append(filter)
                    filter = []

                if "E: activity" in line or "E: receiver" in line or "E: service" in line or "E: uses-permission" in line:
                    return line, component 
                
                try:
                    line = next(gen)
                except StopIteration:
                    return None, component
        except StopIteration:
                traceback.print_exc() 
                pass        

    def get_comp_type(self, line):
        if "E: activity" in line: 
            return "Activity"
        elif "E: receiver" in line: 
            return "Receiver"
        elif "E: service" in line: 
            return "Service"
        else:
            return "Permission"                
        
    def visit_component(self, component, gen):
        line = next(gen)
        name = self.get_x("name", line)
        try:
            while True:
                if "E: activity" in line or "E: receiver" in line or "E: service" in line or "E: uses-permission" in line:
                    return self.visit_component(self.get_comp_type(line), gen)
                
                line = next(gen)                 
                # if "E: activity" in line or "E: receiver" in line or "E: service" in line:
                    # continue
                if "meta-data" in line:   
                    continue     
                
                if "layout" in line:   
                    continue                                 
                
                if "A: " in line:   
                    continue  
                
                if "intent-filter" in line:
                    return self.visit_intent_filter(component, name, line, gen)
                else:    
                    component = dict([("name", name),("type", component), ("intent-filters", [])])
                    return line, component

        except StopIteration:
                component = dict([("name", name),("type", component), ("intent-filters", [])])
                return None, component

    def parse(self):
        generator = self.get_components()
        
        app = dict([("package", self.package), ("permissions", []), ("components", [])])
        permissions = set()
        components = []

        
        line = ""
        try:
            while True:
                if "E: activity" in line or "E: receiver" in line or "E: service" in line or "E: uses-permission" in line:
                    if "E: uses-permission" in line:
                        line = next(generator)
                        permissions.add(self.get_x("name", line))
                        try:
                            line = next(generator)
                        except StopIteration:
                            break   
                        continue
                    line1, component = self.visit_component(self.get_comp_type(line), generator)
                    if component is None:
                        break
                    components.append(component)
                    if line1 is None:
                        break
                    line = line1
                    continue
                
                try:
                    line = next(generator)
                except StopIteration:
                    break
                
            app["components"].append(components)
            app["permissions"].append(list(permissions))
            return json.dumps(app)
        except StopIteration:
            traceback.print_exc() 
            pass            

