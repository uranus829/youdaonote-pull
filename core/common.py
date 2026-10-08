import os
import re
import sys


def get_script_directory() -> str:
    """
    获取脚本/程序所在的主目录绝对路径
    兼顾打包后的 exe 和普通命令行直接执行
    """
    if getattr(sys, "frozen", False):
        # 打包后的可执行文件 (PyInstaller / cx_Freeze)
        return os.path.dirname(os.path.abspath(sys.executable))
    else:
        # 普通 Python 脚本，以执行的主入口文件所在目录为准
        main_file = sys.argv[0] if sys.argv and sys.argv[0] else __file__
        return os.path.dirname(os.path.abspath(main_file))


def sanitize_filename(filename: str, replace_char: str = "_") -> str:
    """
    过滤并清洗 Windows / Linux 非法文件名字符
    非法字符: \\ / : * ? " < > |
    """
    if not filename:
        return ""
    # 替换非法字符
    cleaned = re.sub(r'[\\/:*?"<>|]', replace_char, filename.strip())
    # 去除首尾的多余空格和点（Windows 文件名末尾不允许是点）
    cleaned = cleaned.strip(". ")
    return cleaned if cleaned else "untitled"
