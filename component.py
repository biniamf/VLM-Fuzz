# -- author: Biniam Fisseha Demissie
import ssdeep # for fuzzy hashing

class Component:

    component_id = None
    action = None
    input = None
    comp_class = None
    clickable_bound = None
    resource_id = None
    text = None
    content_desc = None
    expired = None
    ai_input = None
    hash = None
    ignore = False
    label = None
    processed = False
    password = False
    
    def __init__(self, comp_class, component_id, action, input, bound, content_desc, res_id, text, ignore, enabled, bounds, password=False, label=None, processed=False):
        self.component_id = component_id
        self.action = action
        self.input = input
        self.comp_class = comp_class
        self.clickable_bound = bound
        self.content_desc = content_desc
        self.resource_id = res_id
        self.text = text
        self.expired = False
        self.hash = ssdeep.hash(comp_class + component_id + str(bound) + content_desc + res_id + text)
        self.ignore = ignore
        self.enabled = enabled
        self.label = label
        self.password = password
        self.processed = processed        
        self.bounds = bounds

    def set_ai_input(self, input):
        self.ai_input = input

    def compare(self, hash):
        c = ssdeep.compare(self.hash, hash)

        return ssdeep.compare(self.hash, hash)

    def to_dict(self):
        return [{"component_id": self.component_id, "action": str(self.action), "class": self.comp_class, "bounds": self.clickable_bound, "content_description": self.content_desc, "resource_id": self.resource_id, "text": self.text, "label": self.label, "is_password": self.password}]
