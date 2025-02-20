# -- author: Biniam Fisseha Demissie
import os
import base64
import re
import openai 
from openai import OpenAI

open_ai_key = os.getenv("OPENAI_API_KEY") 

client = OpenAI(
    api_key=open_ai_key)

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def process_ai_response_suggestion(response):
    # print(response)
    summary  = ""
    order = ""
    thought = ""
    observation = ""
    navigation = ""

    pattern = r"^Summary:\s(.*)$"
    match = re.findall(pattern, response, re.MULTILINE)
    if match:
        summary = match[0]

    pattern = r"^Steps:\s(.*)$"
    match = re.findall(pattern, response, re.MULTILINE)
    if match:
        order = match[0]

    pattern = r"^Thought:\s(.*)$"
    match = re.findall(pattern, response, re.MULTILINE)
    if match:
        thought = match[0]

    pattern = r"^Observation:\s(.*)$"
    match = re.findall(pattern, response, re.MULTILINE)
    if match:
        observation = match[0]
        
    # pattern = r"^Navigation:\s(.*)$"
    # match = re.findall(pattern, response, re.MULTILINE)
    # if match:
    #     navigation = match[0]          

    return order, summary, thought, observation, response

def open_ai_query(image_path, prompt):
    
    base64_image = encode_image(image_path)
    
    # two attempts
    for _ in range(2):
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                                {
                        "role": "system",
                        "content": "You are a professional app tester. Your goal is to explore all the possible functionalities of the a given app."
                        },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                                "detail": "high"
                            }

                        },
                    ],
                }
            ],
            temperature=0,
            max_tokens=1024
        )
        
        return response.choices[0].message.content     

    
def get_ai_sequence(image_path, prompt, activity_name, summary, labels):

    try:
        prompt = re.sub("<activity_name>", activity_name, prompt)  
        prompt = re.sub("<labels>", labels, prompt)     
        prompt = re.sub("<<summary>>", summary, prompt)      
        # print(prompt)  
        return process_ai_response_suggestion(open_ai_query(image_path, prompt))
    except openai.APIError as e:
        #Handle API error here, e.g. retry or log
        print(f"OpenAI API returned an API Error: {e}")
        return None, None, None, None, None