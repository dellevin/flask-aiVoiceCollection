# -*- coding: utf-8 -*-
"""
fen-ci 分词工具函数
"""
import os
import re
import json

from config import JIEBA_DICT_FILE, TOKEN_FILE_PATH

# 可选依赖
try:
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False


class Tokenizer:
    """分词器：加载词典并提供分词能力"""

    def __init__(self):
        self.simple_words = []
        self.compound_words = set()
        self.compound_pattern = None
        self._load_words()

    def _load_words(self):
        custom_words = set()
        if os.path.exists(JIEBA_DICT_FILE):
            try:
                with open(JIEBA_DICT_FILE, 'r', encoding='utf-8') as f:
                    for line in f:
                        word = line.strip()
                        if word:
                            custom_words.add(word)
            except Exception as e:
                print(f"读取自定义词汇文件失败: {e}")

        compound = {w for w in custom_words if any(c.isupper() for c in w)}
        simple = custom_words - compound
        self.simple_words = list(simple)
        self.compound_words = compound
        if compound:
            self.compound_pattern = re.compile('|'.join(re.escape(w) for w in compound), re.IGNORECASE)
        else:
            self.compound_pattern = None

        if JIEBA_AVAILABLE:
            for w in self.simple_words:
                jieba.add_word(w)
        # print(f"加载了 {len(self.simple_words)} 个简单自定义词汇，{len(self.compound_words)} 个复合词。")

    def tokenize(self, text):
        if not text or not text.strip():
            return []

        tokens = []
        chinese_pattern = re.compile(r'[\u4e00-\u9fff]+')
        english_pattern = re.compile(r'[a-zA-Z]+(?:-[a-zA-Z]+)*')
        number_pattern = re.compile(r'-?\d+\.?\d*')
        pos = 0
        text_length = len(text)

        while pos < text_length:
            char = text[pos]
            if char.isspace():
                pos += 1
                continue

            if self.compound_pattern:
                compound_match = self.compound_pattern.match(text, pos)
                if compound_match:
                    matched_word = compound_match.group()
                    tokens.append({'text': matched_word, 'type': 'compound_eng', 'length': len(matched_word)})
                    pos += len(matched_word)
                    continue

            if chinese_pattern.match(char):
                chinese_str = ''
                while pos < text_length and chinese_pattern.match(text[pos]):
                    chinese_str += text[pos]
                    pos += 1
                if JIEBA_AVAILABLE:
                    chinese_tokens = list(jieba.cut(chinese_str))
                else:
                    chinese_tokens = list(chinese_str)
                for token in chinese_tokens:
                    if token.strip():
                        tokens.append({'text': token, 'type': 'chinese', 'length': len(token)})
            elif english_pattern.match(char):
                match = english_pattern.match(text, pos)
                if match:
                    word = match.group()
                    tokens.append({'text': word, 'type': 'english', 'length': len(word)})
                    pos += len(word)
            elif number_pattern.match(char) or (char == '-' and pos + 1 < text_length and text[pos + 1].isdigit()):
                match = number_pattern.match(text, pos)
                if match:
                    number = match.group()
                    tokens.append({'text': number, 'type': 'number', 'length': len(number)})
                    pos += len(number)
            else:
                tokens.append({'text': char, 'type': 'punctuation', 'length': 1})
                pos += 1

        return tokens

    @staticmethod
    def get_stats(tokens):
        stats = {'total': len(tokens), 'chinese': 0, 'english': 0, 'compound_eng': 0, 'number': 0, 'punctuation': 0}
        for token in tokens:
            t = token['type']
            if t in stats:
                stats[t] += 1
        stats['total'] = sum(v for k, v in stats.items() if k != 'total')
        return stats


class TokenAuth:
    """Token 认证管理"""

    def __init__(self):
        self.valid_tokens = {}
        self._load_tokens()

    def _load_tokens(self):
        if not os.path.exists(TOKEN_FILE_PATH):
            print(f"警告: 找不到Token文件 '{TOKEN_FILE_PATH}'")
            return
        try:
            with open(TOKEN_FILE_PATH, 'r', encoding='utf-8') as f:
                tokens_data = json.load(f)
            token_map = {}
            for item in tokens_data:
                if 'user' in item and 'token' in item:
                    token_map[item['token']] = item['user']
            self.valid_tokens = token_map
            # print(f"成功加载了 {len(token_map)} 个有效Token。")
        except Exception as e:
            print(f"读取Token文件失败: {e}")

    def get_user(self, token):
        return self.valid_tokens.get(token)


# 全局单例
tokenizer = Tokenizer()
token_auth = TokenAuth()
