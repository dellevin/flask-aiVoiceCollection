import chardet

# 自动选择解码格式
def detect_and_decode(data_bytes):
    """使用 chardet 检测编码并解码"""
    # 使用 chardet 检测编码
    detected_encoding_info = chardet.detect(data_bytes)
    detected_encoding = detected_encoding_info.get('encoding')
    confidence = detected_encoding_info.get('confidence', 0)

    if detected_encoding:
        try:
            decoded_string = data_bytes.decode(detected_encoding)
            print(f"  - 使用检测到的编码 '{detected_encoding}' 成功解码，置信度：{confidence}")
            return decoded_string
        except UnicodeDecodeError as e:
            print(f"  - 使用检测到的编码 '{detected_encoding}' 解码失败: {e}")

    # 方法二：如果检测失败或置信度低，尝试常见的编码
    encodings_to_try = ['utf-8', 'gbk', 'gb2312', 'cp936']
    print(f"  - 检测失败/置信度低，尝试常见编码")
    print(f"  - 尝试常见编码列表: {encodings_to_try}")

    for enc in encodings_to_try:
        try:
            decoded_string = data_bytes.decode(enc)
            print(f"  - 成功使用编码 '{enc}' 解码。")
            return decoded_string
        except UnicodeDecodeError as e:
            print(f"  - 尝试编码 '{enc}' 失败: {e}")
            continue
    # 如果所有方法都失败
    print("  - 所有编码尝试均失败。")
    return "所有编码尝试均失败。"
