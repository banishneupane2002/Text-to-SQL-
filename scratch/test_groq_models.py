import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bank_portal.settings')
django.setup()

from groq import Groq
from sql_assistant.engine.config import get_groq_api_key

client = Groq(api_key=get_groq_api_key())
models = ['openai/gpt-oss-20b', 'qwen/qwen3.8-27b', 'groq/compound', 'openai/gpt-oss-120b']

from sql_assistant.engine.pipeline import run_retrieval_pipeline
from sql_assistant.engine.groq_service import build_prompt

q = "Which top 5 sales representatives generated the highest total sales revenue, including their full name and total amount sold?"
ret = run_retrieval_pipeline(q)
messages = build_prompt(q, ret["pruned_schema"])

for m in models:
    print('=========================================')
    print('Testing text_to_sql prompt on model:', m)
    for max_t in [600, 1500]:
        print(f'-- max_tokens={max_t} --')
        try:
            r = client.chat.completions.create(
                model=m,
                messages=messages,
                response_format={'type': 'json_object'},
                max_tokens=max_t
            )
            print('SUCCESS:', r.choices[0].message.content[:200])
            break
        except Exception as e:
            print('ERROR:', e)
