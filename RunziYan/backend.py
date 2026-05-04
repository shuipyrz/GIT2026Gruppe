import os
import json
import re
from typing import TypedDict, Optional
from pathlib import Path
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END

# --- 1. 环境配置 ---
current_dir = Path(__file__).parent
load_dotenv(dotenv_path=current_dir / ".env")

api_key = os.getenv("DEEPSEEK_API_KEY")
if not api_key:
    raise ValueError("DEEPSEEK_API_KEY wurde nicht gefunden!")

# --- 2. 初始化 DeepSeek ---
# backend.py 中的初始化部分
llm = ChatOpenAI(
    model="deepseek-chat", # 或者根据你的测试结果使用 deepseek-v4-flash
    openai_api_key=api_key,
    base_url="https://api.deepseek.com",
    # 增加超时限制，防止连接瞬间中断
    timeout=20, 
    max_retries=2
)

# --- 3. 定义状态 ---
class AgentState(TypedDict):
    input_text: str           
    geojson_data: Optional[dict] 
    error_message: str        

# --- 4. 定义节点函数 ---
def geo_generator(state: AgentState):
    user_input = state.get("input_text", "")
    
    # 强化指令：确保输出多个城市且无 Markdown 代码块
    instruction = f"""
    Du bist ein GIS-Experte. 
    Aufgabe: Erstelle ein valides GeoJSON (FeatureCollection) für ALLE genannten Orte: {user_input}
    Regeln:
    1. Erstelle für JEDEN genannten Ort ein separates Feature.
    2. Properties: Muss 'city' und 'distance_km' enthalten.
    3. Gib NUR das pure JSON zurück, KEIN Markdown (```json).
    """
    
    try:
        response = llm.invoke(instruction)
        content = response.content
        
        # 提取 JSON 的正则表达式
        json_match = re.search(r'(\{.*\})', content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(1))
            return {"geojson_data": data, "error_message": ""}
        else:
            return {"error_message": f"Kein JSON gefunden. AI-Output war: {content[:100]}..."}
    except Exception as e:
        return {"error_message": str(e)}

# --- 5. 构建图 ---
workflow = StateGraph(AgentState)
workflow.add_node("generator", geo_generator)
workflow.add_edge(START, "generator")
workflow.add_edge("generator", END)

agent_app = workflow.compile()
