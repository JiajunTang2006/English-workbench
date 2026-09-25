import os
import sys

# 让 server 成为可导入的包：把 teachmate 根目录（server 的父目录）加入 sys.path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
