#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import os
import platform
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime
from enum import Enum
from typing import Tuple

import requests
from win32_setctime import setctime

from core import log
from core.api import YoudaoNoteApi
from core.common import get_script_directory
from core.covert import YoudaoNoteConvert
from core.image import ImagePull

__author__ = "Depp Wang (deppwxq@gmail.com)"
__github__ = "https//github.com/DeppWang/youdaonote-pull"

REGEX_SYMBOL = re.compile(r'[\\/:\*\?"<>\|]')  # 符号：\ / : * ? " < > |
MARKDOWN_SUFFIX = ".md"
SYNC_MAP_FILENAME = ".sync_map.json"


class FileType(Enum):
    OTHER = 0
    MARKDOWN = 1
    XML = 2
    JSON = 3
    HTML = 4
    PLAIN_TEXT = 5


class FileActionEnum(Enum):
    CONTINUE = "跳过"
    ADD = "新增"
    UPDATE = "更新"


class YoudaoNotePull(object):
    """
    有道云笔记 Pull 封装（带本地 ID 映射与孤儿文件清理）
    """

    def __init__(self):
        self.root_local_dir = None  # 本地文件根目录
        self.youdaonote_api = None
        self.smms_secret_token = None
        self.is_relative_path = None  # 是否使用相对路径
        self.insert_timestamps = None  # 是否插入时间戳
        self.sync_map_file = None  # 映射表文件路径
        self.sync_map = {}  # file_id -> {rel_path, modify_time, name}
        self.synced_file_ids = set()  # 本次遍历到的有效 file_id 集合

    def _load_sync_map(self):
        """读取本地 .sync_map.json 映射索引"""
        self.sync_map_file = os.path.join(self.root_local_dir, SYNC_MAP_FILENAME)
        if os.path.exists(self.sync_map_file):
            try:
                with open(self.sync_map_file, "r", encoding="utf-8") as f:
                    self.sync_map = json.load(f)
                logging.info("已成功加载本地同步映射索引，共管理 %d 篇笔记", len(self.sync_map))
            except Exception as e:
                logging.warning("读取 .sync_map.json 失败，将重新初始化：%s", e)
                self.sync_map = {}
        else:
            self.sync_map = {}

    def _save_sync_map(self):
        """保存 .sync_map.json 映射索引"""
        if not self.sync_map_file:
            return
        try:
            with open(self.sync_map_file, "w", encoding="utf-8") as f:
                json.dump(self.sync_map, f, ensure_ascii=False, indent=2)
            logging.info("同步映射索引已更新保存")
        except Exception as e:
            logging.warning("保存 .sync_map.json 失败：%s", e)

    def _clean_deleted_files(self):
        """
        对比云端与本地映射，自动同步删除云端已废弃的笔记
        """
        deleted_ids = []
        for fid, item in self.sync_map.items():
            if fid not in self.synced_file_ids:
                rel_path = item.get("rel_path")
                if rel_path:
                    abs_path = os.path.join(self.root_local_dir, rel_path).replace("\\", "/")
                    if os.path.exists(abs_path):
                        try:
                            os.remove(abs_path)
                            logging.info("检测到云端已删除该笔记，同步删除本地文件：「%s」", rel_path)
                        except Exception as e:
                            logging.warning("删除本地废弃文件「%s」失败: %s", rel_path, e)
                deleted_ids.append(fid)

        for fid in deleted_ids:
            self.sync_map.pop(fid, None)

    def _covert_config(self, config_path=None) -> Tuple[dict, str]:
        """转换配置文件为 dict"""
        config_path = (
            config_path
            if config_path
            else os.path.join(get_script_directory(), "config.json")
        )
        with open(config_path, "rb") as f:
            config_str = f.read().decode("utf-8")

        try:
            config_dict = json.loads(config_str)
        except Exception:
            return (
                {},
                "请检查「config.json」格式是否为 utf-8 格式的 json！建议使用 VSCode/Sublime 编辑「config.json」",
            )

        key_list = ["local_dir", "ydnote_dir", "smms_secret_token", "is_relative_path", "insert_timestamps"]
        if key_list != list(config_dict.keys()):
            return (
                {},
                "请检查「config.json」的 key 是否分别为 local_dir, ydnote_dir, smms_secret_token, is_relative_path, insert_timestamps",
            )
        return config_dict, ""

    def _check_local_dir(self, local_dir, test_default_dir=None) -> Tuple[str, str]:
        """检查本地文件夹"""
        if not local_dir:
            add_dir = test_default_dir if test_default_dir else "youdaonote"
            local_dir = os.path.join(get_script_directory(), add_dir).replace("\\", "/")

        if not os.path.exists(local_dir):
            try:
                os.makedirs(local_dir, exist_ok=True)
            except Exception:
                return "", "请检查「{}」上层文件夹是否存在，并使用绝对路径！".format(local_dir)
        return local_dir, ""

    def _get_ydnote_dir_id(self, ydnote_dir) -> Tuple[str, str]:
        """获取指定有道云笔记指定目录 ID"""
        root_dir_info = self.youdaonote_api.get_root_dir_info_id()
        root_dir_id = root_dir_info["fileEntry"]["id"]

        if not ydnote_dir:
            return root_dir_id, ""

        dir_info = self.youdaonote_api.get_dir_info_by_id(root_dir_id)
        for entry in dir_info["entries"]:
            file_entry = entry["fileEntry"]
            if file_entry["name"] == ydnote_dir:
                return file_entry["id"], ""

        return "", "有道云笔记指定顶层目录不存在"

    def get_ydnote_dir_id(self) -> Tuple[str, str]:
        """获取有道云笔记根目录或指定目录 ID"""
        config_dict, error_msg = self._covert_config()
        if error_msg:
            return "", error_msg
        local_dir, error_msg = self._check_local_dir(local_dir=config_dict["local_dir"])
        if error_msg:
            return "", error_msg
        self.root_local_dir = local_dir
        self.youdaonote_api = YoudaoNoteApi()
        error_msg = self.youdaonote_api.login_by_cookies()
        logging.info("本次使用 Cookies 登录")
        if error_msg:
            return "", error_msg
        self.smms_secret_token = config_dict["smms_secret_token"]
        self.is_relative_path = config_dict["is_relative_path"]
        self.insert_timestamps = config_dict.get("insert_timestamps", False)

        # 加载本地映射缓存表
        self._load_sync_map()

        return self._get_ydnote_dir_id(ydnote_dir=config_dict["ydnote_dir"])

    def _judge_type(self, file_id, youdao_file_suffix) -> Enum:
        """判断笔记类型"""
        file_type = FileType.OTHER
        if youdao_file_suffix == MARKDOWN_SUFFIX:
            file_type = FileType.MARKDOWN
            return file_type
        elif (
            youdao_file_suffix == ".note"
            or youdao_file_suffix == ".clip"
            or youdao_file_suffix == ""
        ):
            response = self.youdaonote_api.get_file_by_id(file_id)
            if response.content[:5] == b"<?xml":
                file_type = FileType.XML
            elif response.content.startswith(b'{"'):
                file_type = FileType.JSON
            elif (b'<div' in response.content or b'<br' in response.content or 
                  b'<span' in response.content or b'<p>' in response.content or
                  b'</div>' in response.content or b'</span>' in response.content):
                file_type = FileType.HTML
            else:
                file_type = FileType.PLAIN_TEXT
        return file_type

    def _get_file_action(self, local_file_path, modify_time) -> Enum:
        """获取文件操作行为（新增 / 跳过 / 更新）"""
        if not os.path.exists(local_file_path):
            return FileActionEnum.ADD

        local_mtime = os.path.getmtime(local_file_path)
        # 精确对比时间戳（允许 1 秒以内的微差）
        if modify_time <= local_mtime + 1:
            logging.info("此文件「%s」不更新，跳过", local_file_path)
            return FileActionEnum.CONTINUE

        return FileActionEnum.UPDATE

    def _optimize_file_name(self, name) -> str:
        """优化文件名（清洗非法特殊字符）"""
        regex_symbol = re.compile(r"[<]")
        del_regex_symbol = re.compile(r'[\\/":\|\*\?#>\t\r\n]')
        name = name.replace("\n", "").replace("\t", "").replace("\r", "")
        name = name.strip()
        name = regex_symbol.sub("_", name)
        name = del_regex_symbol.sub("", name)
        return name

    def pull_dir_by_id_recursively(self, dir_id, local_dir):
        """递归下载目录下所有文件"""
        dir_info = self.youdaonote_api.get_dir_info_by_id(dir_id)
        try:
            entries = dir_info["entries"]
        except KeyError:
            raise KeyError("有道云笔记修改了接口地址，此脚本暂时不能使用！请提 issue")

        for entry in entries:
            file_entry = entry["fileEntry"]
            fid = file_entry["id"]
            name = file_entry["name"]
            if file_entry["dir"]:
                sub_dir = os.path.join(local_dir, name).replace("\\", "/")
                if not os.path.exists(sub_dir):
                    os.makedirs(sub_dir, exist_ok=True)
                self.pull_dir_by_id_recursively(fid, sub_dir)
            else:
                modify_time = file_entry["modifyTimeForSort"]
                create_time = file_entry["createTimeForSort"]
                self._add_or_update_file(fid, name, local_dir, modify_time, create_time)

    def _add_or_update_file(
        self, file_id, file_name, local_dir, modify_time, create_time
    ):
        """
        新增、重命名或更新文件
        """
        # 记录本次扫描到的有效 ID（用于后续判定孤儿文件）
        self.synced_file_ids.add(file_id)

        clean_file_name = self._optimize_file_name(file_name)
        youdao_file_suffix = os.path.splitext(clean_file_name)[1]
        original_file_path = os.path.join(local_dir, clean_file_name).replace("\\", "/")

        file_type = self._judge_type(file_id, youdao_file_suffix)

        local_file_path = (
            os.path.join(
                local_dir, "".join([os.path.splitext(clean_file_name)[0], MARKDOWN_SUFFIX])
            ).replace("\\", "/")
            if file_type != FileType.OTHER
            else original_file_path
        )

        # ----------------- 核心特性：自动检测重命名并处理 -----------------
        current_rel_path = os.path.relpath(local_file_path, self.root_local_dir).replace("\\", "/")
        old_info = self.sync_map.get(file_id)

        if old_info:
            old_rel_path = old_info.get("rel_path")
            if old_rel_path and old_rel_path != current_rel_path:
                old_abs_path = os.path.join(self.root_local_dir, old_rel_path).replace("\\", "/")
                if os.path.exists(old_abs_path):
                    try:
                        os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                        if os.path.exists(local_file_path):
                            os.remove(local_file_path)
                        os.rename(old_abs_path, local_file_path)
                        logging.info("🎯 检测到笔记重命名，已将本地「%s」自动更名为「%s」", old_rel_path, current_rel_path)
                    except Exception as e:
                        logging.warning("自动重命名本地文件失败: %s", e)
        # ----------------------------------------------------------------

        tip = (
            "，云笔记原格式为 {}".format(file_type.name) if file_type != FileType.OTHER else ""
        )

        file_action = self._get_file_action(local_file_path, modify_time)
        if file_action == FileActionEnum.CONTINUE:
            # 即使内容跳过下载，也更新映射表中的相对路径
            self.sync_map[file_id] = {
                "rel_path": current_rel_path,
                "modify_time": modify_time,
                "name": clean_file_name
            }
            return

        if file_action == FileActionEnum.UPDATE:
            try:
                os.remove(local_file_path)
            except Exception:
                pass

        try:
            self._pull_file(
                file_id,
                original_file_path,
                local_file_path,
                file_type,
                youdao_file_suffix,
                create_time,
                modify_time,
            )

            # ----------------- 核心时间戳修改：100% 对齐有道云时间 -----------------
            os.utime(local_file_path, (modify_time, modify_time))
            if platform.system() == "Windows":
                setctime(local_file_path, create_time)
            # -------------------------------------------------------------------

            # 更新映射字典
            self.sync_map[file_id] = {
                "rel_path": current_rel_path,
                "modify_time": modify_time,
                "name": clean_file_name
            }

            if file_action == FileActionEnum.CONTINUE:
                logging.debug(
                    "{}「{}」{}".format(file_action.value, local_file_path, tip)
                )
            else:
                logging.info("{}「{}」{}".format(file_action.value, local_file_path, tip))

        except Exception as error:
            logging.info(
                "{}「{}」可能失败！请检查文件！错误提示：{}".format(
                    file_action.value, original_file_path, format(error)
                )
            )

    def _pull_file(
        self, file_id, file_path, local_file_path, file_type, youdao_file_suffix, create_time, modify_time
    ):
        """下载并转换文件内容"""
        # 1、下载原始内容
        response = self.youdaonote_api.get_file_by_id(file_id)
        with open(file_path, "wb") as f:
            f.write(response.content)

        # 2、转换为 Markdown 类型
        if file_type == FileType.XML:
            try:
                YoudaoNoteConvert.covert_xml_to_markdown(file_path, self.insert_timestamps, create_time, modify_time)
            except ET.ParseError:
                logging.info("此 note 笔记为 17 年以前早期格式，将转换为 Markdown ...")
                YoudaoNoteConvert.covert_html_to_markdown(file_path, self.insert_timestamps, create_time, modify_time)
            except Exception as e:
                logging.info("note 笔记转换 MarkDown 失败，将跳过", repr(e))
        elif file_type == FileType.JSON:
            YoudaoNoteConvert.covert_json_to_markdown(file_path, self.insert_timestamps, create_time, modify_time)
        elif file_type == FileType.HTML:
            logging.info("此 note 笔记为 HTML 格式，将转换为 Markdown ...")
            YoudaoNoteConvert.covert_html_to_markdown(file_path, self.insert_timestamps, create_time, modify_time)
        elif file_type == FileType.PLAIN_TEXT:
            logging.info("此 note 笔记为纯文本格式，将重命名为 Markdown ...")
            base = os.path.splitext(file_path)[0]
            new_file_path = "".join([base, MARKDOWN_SUFFIX])
            os.rename(file_path, new_file_path)
            if self.insert_timestamps:
                YoudaoNoteConvert._insert_timestamps_to_file(new_file_path, create_time, modify_time)

        # 3、迁移正文中的图片及附件资源链接
        if file_type != FileType.OTHER or youdao_file_suffix == MARKDOWN_SUFFIX:
            imagePull = ImagePull(
                self.youdaonote_api, self.smms_secret_token, self.is_relative_path
            )
            imagePull.migration_ydnote_url(local_file_path)


if __name__ == "__main__":
    log.init_logging()

    start_time = int(time.time())

    try:
        youdaonote_pull = YoudaoNotePull()
        ydnote_dir_id, error_msg = youdaonote_pull.get_ydnote_dir_id()
        if error_msg:
            logging.info(error_msg)
            sys.exit(1)
        logging.info("正在 pull，请稍后 ...")
        youdaonote_pull.pull_dir_by_id_recursively(
            ydnote_dir_id, youdaonote_pull.root_local_dir
        )

        # ----------------- 遍历结束：清理云端已删除文件并保存索引 -----------------
        youdaonote_pull._clean_deleted_files()
        youdaonote_pull._save_sync_map()
        # --------------------------------------------------------------------

    except requests.exceptions.ProxyError:
        logging.info(
            "请检查网络代理设置；也有可能是调用有道云笔记接口次数达到限制，请等待一段时间后重新运行脚本，若一直失败，可删除「cookies.json」后重试"
        )
        traceback.print_exc()
        logging.info("已终止执行")
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        logging.info("网络错误，请检查网络是否正常连接。若突然执行中断，可忽略此错误，重新运行脚本")
        traceback.print_exc()
        logging.info("已终止执行")
        sys.exit(1)
    except Exception as err:
        logging.info("Cookies 可能已过期！其他错误：", format(err))
        traceback.print_exc()
        logging.info("已终止执行")
        sys.exit(1)

    end_time = int(time.time())
    logging.info("运行完成！耗时 {} 秒".format(str(end_time - start_time)))
