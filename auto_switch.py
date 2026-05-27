import os
import json
import requests
import re
from datetime import datetime

# 文件路径配置
JSON_PATH = "models_config.json"
YAML_PATH = "config/config.yaml"

def check_model_alive(api_key, model_name):
    """检测模型是否可用，增加对多模态模型报错的兼容性"""
    url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # 采用更通用的输入格式
    data = {
        "model": model_name,
        "input": {"messages": [{"role": "user", "content": "p"}]},
        "parameters": {"max_tokens": 1}
    }
    
    try:
        res = requests.post(url, headers=headers, json=data, timeout=15)
        
        # 情况 1: 成功返回，模型肯定活着
        if res.status_code == 200:
            return True
        
        # 情况 2: 失败返回，解析错误原因
        response_data = res.json()
        error_code = response_data.get("code", "")
        error_message = response_data.get("message", "")
        
        # 只有明确是额度不足、过期或账号受限时才判定为“死亡”
        # 402 是余额不足，Quota 是配额限制
        kill_signals = ["Quota", "InsufficientBalance", "DataOutOfRange", "LimitReached"]
        if res.status_code == 402 or any(sig in error_code or sig in error_message for sig in kill_signals):
            print(f"DEBUG: {model_name} 确认额度已耗尽或过期: {error_message}")
            return False
            
        # 情况 3: 其他报错（如参数错误 400 等）
        # 说明模型活着，只是我们发送的探测请求格式（对 VL 模型）不完全正确
        print(f"DEBUG: {model_name} 返回非欠费错误 ({res.status_code}: {error_code})，判定为存活。")
        return True
        
    except Exception as e:
        print(f"ERROR: 网络请求异常: {e}")
        # 网络抖动暂不剔除，返回 True 保留模型
        return True

def update_yaml_safe(new_model):
    """精准修改 YAML 第 418 行附近的配置"""
    if not os.path.exists(YAML_PATH):
        print(f"Error: {YAML_PATH} 不存在")
        return False
        
    with open(YAML_PATH, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    updated = False
    # 在 418 行前后 20 行内搜索关键字，提高容错率
    search_range = range(max(0, 400), min(len(lines), 440)) 
    for i in search_range:
        if "model:" in lines[i] and "dashscope/" in lines[i]:
            # 正则替换引号内的模型路径
            lines[i] = re.sub(r'(model:\s*")dashscope/[^"]+(")', rf'\1dashscope/{new_model}\2', lines[i])
            updated = True
            break
    
    if updated:
        with open(YAML_PATH, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        return True
    return False

def main():
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        print("CRITICAL: 环境变量 DASHSCOPE_API_KEY 未设置！")
        return

    if not os.path.exists(JSON_PATH):
        print(f"Error: {JSON_PATH} 不存在")
        return

    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 脚本运行时的北京时间判断（GitHub Action 环境需注意时区，这里简单按日期判断）
    today = datetime.now().strftime('%Y-%m-%d')
    changed = False
    final_model = None

    # 循环检测并彻底剔除失效模型
    while config.get('active_models'):
        current = config['active_models'][0]
        model_name = current['name']
        expiry = current['expiry']
        
        print(f"正在检测: {model_name} (有效期至: {expiry})")
        
        # 1. 检查日期
        is_expired = today > expiry
        # 2. 检查额度
        is_alive = False if is_expired else check_model_alive(api_key, model_name)

        if is_expired or not is_alive:
            reason = "过期" if is_expired else "额度用尽"
            print(f">>> [剔除] {model_name} 原因: {reason}")
            
            # 直接删除失效项
            config['active_models'].pop(0)
            changed = True
        else:
            # 找到第一个既没过期又有额度的模型
            print(f">>> [可用] {model_name}")
            final_model = model_name
            break

    # 执行更新和保存
    if changed:
        if final_model:
            update_yaml_safe(final_model)
            print(f"YAML 已同步更新为: {final_model}")
        else:
            print("警告：所有模型都已失效，队列已空！")
        
        # 保存精简后的 JSON
        with open(JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    else:
        print("队首模型状态正常，未发生变动。")

if __name__ == "__main__":
    main()
