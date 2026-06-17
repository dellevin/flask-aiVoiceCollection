# -*- coding: utf-8 -*-
"""
content-tag 文章标签生成蓝图
支持 Ollama (/api/generate) 和 llama.cpp/OpenAI 兼容 (/v1/chat/completions)
"""
import requests
import re
import time
import json

from flask import Blueprint, render_template, request, jsonify
from utils.stats_db import get_content_tag_settings, update_content_tag_settings

bp = Blueprint('content_tag', __name__, url_prefix='/content-tag')


@bp.route('/')
def page():
    return render_template('content_tag.html')


@bp.route('/settings', methods=["GET"])
def get_settings():
    return jsonify({"success": True, "settings": get_content_tag_settings()})


@bp.route('/settings', methods=["POST"])
def save_settings():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "缺少数据"}), 400
    update_content_tag_settings(data)
    return jsonify({"success": True, "settings": get_content_tag_settings()})


@bp.route('/models', methods=["POST"])
def list_models():
    """获取 Ollama 可用模型列表"""
    data = request.get_json()
    api_url = (data.get("api_url") or "").strip()

    if not api_url:
        return jsonify({"success": False, "error": "请填写 API 地址"})

    # /api/generate -> base url -> /api/tags
    base_url = api_url.rsplit('/api/', 1)[0]
    tags_url = base_url + '/api/tags'

    try:
        r = requests.get(tags_url, timeout=10)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            return jsonify({"success": True, "models": models})
        else:
            return jsonify({"success": False, "error": f"获取模型失败: {r.status_code}"})
    except requests.exceptions.ConnectionError:
        return jsonify({"success": False, "error": f"无法连接到: {tags_url}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@bp.route('/test-api', methods=["POST"])
def test_api():
    data = request.get_json()
    api_url = (data.get("api_url") or "").strip()
    model = (data.get("model") or "").strip()
    software = data.get("software", "llamacpp")

    if not api_url:
        return jsonify({"success": False, "error": "请填写 API 地址"})

    try:
        if software == "ollama":
            base_url = api_url.rsplit('/api/', 1)[0]
            r = requests.get(base_url, timeout=10)
            if r.status_code != 200 or 'Ollama is running' not in r.text:
                return jsonify({"success": False, "error": f"Ollama 未运行: {base_url}"})
            tags_url = base_url + '/api/tags'
            r2 = requests.get(tags_url, timeout=10)
            if r2.status_code != 200:
                return jsonify({"success": False, "error": "Ollama 获取模型列表失败"})
            models = [m["name"] for m in r2.json().get("models", [])]
            return jsonify({"success": True, "message": f"连接成功，共 {len(models)} 个模型", "models": models})
        else:
            payload = {"model": model or "test", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}
            r = requests.post(api_url, json=payload, timeout=10)
            if r.status_code == 200:
                result = r.json()
                name = result.get("model", model)
                return jsonify({"success": True, "message": f"连接成功，模型: {name}"})
            else:
                return jsonify({"success": False, "error": f"API 返回 {r.status_code}: {r.text[:200]}"})
    except requests.exceptions.ConnectionError:
        return jsonify({"success": False, "error": f"无法连接到: {api_url}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@bp.route('/generate', methods=["POST"])
def generate_tags():
    data = request.get_json()
    api_url = (data.get("api_url") or "").strip()
    model = (data.get("model") or "").strip()
    software = data.get("software", "llamacpp")
    content = (data.get("content") or "").strip()
    max_tags = data.get("max_tags", 10)
    min_length = data.get("min_length", 2)
    max_length = data.get("max_length", 6)

    if not api_url:
        return jsonify({"success": False, "error": "请填写 API 地址"})
    if software == "ollama" and not model:
        return jsonify({"success": False, "error": "Ollama 需要填写模型名称"})
    if not content:
        return jsonify({"success": False, "error": "请输入文章内容"})

    custom_system = (data.get("system_msg") or "").strip()
    custom_user = (data.get("user_msg") or "").strip()

    system_msg = custom_system if custom_system else "你是一个专门生成文章标签的助手，请你根据我给你的文章的内容总结并生成一系列的标签，格式可以参考[关键词1, 关键词2, 关键词3].你只需要给我生成这种形式的标签即可，其他分析内容无需输出."
    user_template = custom_user if custom_user else "请严格按照以下要求，从提供的文章内容中提取关键词。\n\n文章内容:\n{content}\n\n要求:\n- 提取最多 {max_tags} 个最能概括文章主旨和核心概念的关键词。\n- 关键词必须来源于文章内容，准确反映文章主题。\n- 每个关键词的长度必须在 {min_length} 到 {max_length} 个字符之间。\n- 输出格式为：关键词1, 关键词2, 关键词3, ...\n- 只输出关键词列表，不要有任何其他解释或前缀。"

    user_msg = user_template.format(content=content, max_tags=max_tags, min_length=min_length, max_length=max_length)

    if software == "ollama":
        payload = {
            "model": model,
            "prompt": user_msg,
            "system": system_msg,
            "stream": False,
            "think": False,
            "options": {"top_p": 0.9, "temperature": 0.1, "num_predict": 2048}
        }
    else:
        payload = {
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ],
            "stream": False,
            "temperature": 0.1,
            "top_p": 0.9,
            "max_tokens": 2048,
        }
        if model:
            payload["model"] = model

    start_time = time.time()

    try:
        response = requests.post(api_url, json=payload, timeout=120)

        if response.status_code != 200:
            return jsonify({"success": False, "error": f"API 返回错误: {response.status_code} - {response.text[:200]}"})

        result = response.json()

        if software == "ollama":
            llm_output = result.get("response", "").strip()
            actual_model = result.get("model", model)
        else:
            choices = result.get("choices", [])
            if not choices:
                return jsonify({"success": False, "error": f"API 响应无 choices: {json.dumps(result, ensure_ascii=False)[:200]}"})
            llm_output = choices[0].get("message", {}).get("content", "").strip()
            actual_model = result.get("model", model)

        think_match = re.search(r'<think>(.*?)</think>', llm_output, re.DOTALL)
        ai_think = think_match.group(1).strip() if think_match else ""

        clean_output = re.sub(r'<think>.*?</think>', '', llm_output, count=1, flags=re.DOTALL).strip()
        raw_tags = [tag.strip() for tag in re.split(r'[,，;；\n]+', clean_output) if tag.strip()]

        seen = set()
        tags = []
        for tag in raw_tags:
            if tag not in seen:
                seen.add(tag)
                tags.append(tag)

        elapsed = round(time.time() - start_time, 2)

        return jsonify({
            "success": True,
            "tags": tags,
            "think": ai_think,
            "model": actual_model,
            "consume": elapsed
        })

    except requests.exceptions.ConnectionError:
        return jsonify({"success": False, "error": f"无法连接到 API 地址: {api_url}"})
    except requests.exceptions.Timeout:
        return jsonify({"success": False, "error": "API 请求超时（120秒）"})
    except Exception as e:
        return jsonify({"success": False, "error": f"请求失败: {str(e)}"})
