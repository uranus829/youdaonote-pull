import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Tuple

MARKDOWN_SUFFIX = ".md"


class XmlElementConvert(object):
    """
    XML Element 转换规则
    """

    @staticmethod
    def parse_element_rich_text(element) -> str:
        """
        深度解析 XML 节点中的富文本
        彻底解决：
        1. 遇到颜色标注后后续内容被丢弃的 Bug
        2. 字体颜色与背景底色无法导出的问题
        3. 超链接丢失的问题
        """
        line_parts = []

        # 1. 如果当前节点开头就有自身文本
        if element.text:
            line_parts.append(element.text)

        # 2. 连续遍历所有子节点
        for child in list(element):
            tag_name = child.tag.replace("{http://note.youdao.com}", "").lower()
            child_text = child.text if child.text else ""

            # 提取可能存在的超链接属性
            href = (
                child.attrib.get("href")
                or child.attrib.get("url")
                or child.attrib.get("resource")
            )

            # 提取可能存在的颜色属性
            color = (
                child.attrib.get("color")
                or child.attrib.get("fc")
                or child.attrib.get("font-color")
            )
            # 提取可能存在的背景底色属性
            bgcolor = (
                child.attrib.get("bgcolor")
                or child.attrib.get("bc")
                or child.attrib.get("background-color")
                or child.attrib.get("highlight")
            )

            # A. 处理超链接
            if href or tag_name in ["a", "link", "hyperlink", "url"]:
                link_url = href if href else child_text
                display_text = child_text if child_text else link_url
                part = f"[{display_text}]({link_url})"
            else:
                part = child_text

                # B. 处理文字样式（粗体、斜体、删除线）
                is_bold = child.attrib.get("bold") == "true" or child.attrib.get("b") == "true" or tag_name == "b"
                is_italic = child.attrib.get("italic") == "true" or child.attrib.get("i") == "true" or tag_name == "i"
                is_strike = child.attrib.get("strike") == "true" or child.attrib.get("s") == "true" or tag_name in ["s", "strike"]

                if is_strike:
                    part = f"~~{part}~~"
                if is_bold and is_italic:
                    part = f"***{part}***"
                elif is_bold:
                    part = f"**{part}**"
                elif is_italic:
                    part = f"*{part}*"

                # C. 处理背景高亮底色 (黄色底色等) -> 转换为 Obsidian/Typora 通用的 ==高亮==
                if bgcolor or "highlight" in tag_name or tag_name == "mark":
                    part = f"=={part}=="

                # D. 处理字体颜色 (红色字体等) -> 转换为兼容 HTML font 标签
                if color and part:
                    part = f'<font color="{color}">{part}</font>'

            line_parts.append(part)

            # 3. 关键点：必须提取 child 标签后的尾随文本 (tail)
            # 否则标签后面的所有文字都会被截断丢失！
            if child.tail:
                line_parts.append(child.tail)

        return "".join(line_parts).strip()

    @staticmethod
    def convert_para_func(**kwargs):
        """正常段落文本（支持颜色、底色高亮、粗体、斜体、超链接）"""
        return kwargs.get("text", "")

    @staticmethod
    def convert_heading_func(**kwargs):
        """标题"""
        level = kwargs.get("element").attrib.get("level", 1)
        level = 1 if str(level).lower() in ["a", "b"] else level
        try:
            level_num = int(level)
        except Exception:
            level_num = 1
        text = kwargs.get("text", "")
        return f"{'#' * level_num} {text}" if text else ""

    @staticmethod
    def convert_image_func(**kwargs):
        """图片"""
        image_url = XmlElementConvert.get_text_by_key(
            list(kwargs.get("element")), "source"
        )
        return "![{text}]({image_url})".format(
            text=kwargs.get("text", ""), image_url=image_url
        )

    @staticmethod
    def convert_attach_func(**kwargs):
        """附件"""
        element = kwargs.get("element")
        filename = XmlElementConvert.get_text_by_key(list(element), "filename")
        resource_url = XmlElementConvert.get_text_by_key(list(element), "resource")
        return "[{text}]({resource_url})".format(
            text=filename, resource_url=resource_url
        )

    @staticmethod
    def convert_code_func(**kwargs):
        """代码块"""
        language = XmlElementConvert.get_text_by_key(
            list(kwargs.get("element")), "language"
        )
        return "```{language}\r\n{code}```".format(
            language=language, code=kwargs.get("text", "")
        )

    @staticmethod
    def convert_todo_func(**kwargs):
        """to-do"""
        return "- [ ] {text}".format(text=kwargs.get("text", ""))

    @staticmethod
    def convert_quote_func(**kwargs):
        """引用"""
        return "> {text}".format(text=kwargs.get("text", ""))

    @staticmethod
    def convert_horizontal_line_func(**kwargs):
        """分割线"""
        return "---"

    @staticmethod
    def convert_list_item_func(**kwargs):
        """列表"""
        list_id = kwargs.get("element").attrib.get("list-id")
        list_type = kwargs.get("list_item", {}).get(list_id, "unordered")
        text = kwargs.get("text", "")
        if list_type == "unordered":
            return f"- {text}"
        elif list_type == "ordered":
            return f"1. {text}"
        return f"- {text}"

    @staticmethod
    def convert_table_func(**kwargs):
        """表格转换"""
        element = kwargs.get("element")
        content = XmlElementConvert.get_text_by_key(element, "content")
        if not content:
            return ""

        nl = "\r\n"
        try:
            table_data = json.loads(content)
        except Exception:
            return ""

        table_data_len = len(table_data.get("widths", []))
        if table_data_len == 0:
            return ""

        table_data_arr = []
        table_data_line = []

        for cells in table_data.get("cells", []):
            values = cells.get("value", "")
            if values is None:
                values = ""
            cell_value = XmlElementConvert._encode_string_to_md(str(values))
            table_data_line.append(cell_value)
            if len(table_data_line) == table_data_len:
                table_data_arr.append(table_data_line)
                table_data_line = []

        if len(table_data_arr) == 1:
            table_data_arr.insert(0, [" " for _ in range(table_data_len)])
            table_data_arr.insert(1, ["-" for _ in range(table_data_len)])
        elif len(table_data_arr) > 1:
            table_data_arr.insert(1, ["-" for _ in range(table_data_len)])

        table_data_str = ""
        for table_line in table_data_arr:
            table_data_str += "|"
            for td in table_line:
                table_data_str += f" {td} |"
            table_data_str += nl

        return table_data_str

    @staticmethod
    def get_text_by_key(element_children, key="text"):
        """获取指定 key 的文本内容"""
        for sub_element in element_children:
            if key in sub_element.tag:
                return sub_element.text if sub_element.text else ""
        return ""

    @staticmethod
    def _encode_string_to_md(original_text):
        """将字符串转义防止 markdown 识别错误"""
        if not original_text or original_text == " ":
            return original_text

        original_text = original_text.replace("\\", "\\\\")
        original_text = original_text.replace("*", "\\*")
        original_text = original_text.replace("_", "\\_")
        original_text = original_text.replace("#", "\\#")
        original_text = original_text.replace("&", "&amp;")
        original_text = original_text.replace("<", "&lt;")
        original_text = original_text.replace(">", "&gt;")
        original_text = original_text.replace("“", "&quot;")
        original_text = original_text.replace("‘", "&apos;")
        original_text = original_text.replace("\t", "&emsp;")
        original_text = original_text.replace("\r\n", "<br>")
        original_text = original_text.replace("\n\r", "<br>")
        original_text = original_text.replace("\r", "<br>")
        original_text = original_text.replace("\n", "<br>")
        return original_text


class JsonConvert(object):
    """
    JSON 转换规则
    """

    def _convert_text_attribute(self, text: str, text_attrs: list) -> str:
        """
        转换文本属性：支持加粗、斜体、删除线、下划线、字体颜色以及背景底色高亮
        """
        if not isinstance(text_attrs, list) or not text_attrs or not text:
            return text

        for attr in text_attrs:
            attr_type = str(attr.get("2", ""))
            attr_val = str(attr.get("3", "")).strip()

            # 1. 基础样式
            if attr_type == "b":
                text = f"**{text}**"
            elif attr_type == "i":
                text = f"*{text}*"
            elif attr_type in ["s", "st", "d"]:
                text = f"~~{text}~~"
            elif attr_type == "u":
                text = f"<u>{text}</u>"

            # 2. 背景高亮底色 (例如黄色底色) -> 转换为 ==高亮==
            elif attr_type in ["bc", "bg", "hl"]:
                text = f"=={text}=="

            # 3. 字体颜色 (例如红色字体) -> 转换为 HTML font
            elif attr_type in ["fc", "c", "color"] and attr_val:
                text = f'<font color="{attr_val}">{text}</font>'

        return text

    def _parse_rich_inline_content(self, block: dict) -> str:
        """
        全量递归提取块内的所有行内富文本
        彻底解决：
        1. 原代码写死 five_contents[0] 导致的颜色变色后文字截断
        2. 列表、引用、表格等丢失超链接与样式
        """
        all_text = ""
        items = block.get("5", [])
        if not items:
            return all_text

        for item in items:
            text_type = item.get("6")
            sub_five = item.get("5")
            seven_contents = item.get("7")

            # A. 超链接类型 (6 == "li")
            if text_type == "li":
                link_url = item.get("4", {}).get("hf", "")
                link_text = ""
                if sub_five:
                    link_text = self._parse_rich_inline_content(item)
                elif seven_contents:
                    for sc in seven_contents:
                        raw = sc.get("8", "")
                        attrs = sc.get("9")
                        if attrs:
                            raw = self._convert_text_attribute(raw, attrs)
                        link_text += raw

                if link_url:
                    all_text += f"[{link_text or link_url}]({link_url})"
                else:
                    all_text += link_text

            # B. 普通带样式的文本段 (7 包含 8文字 和 9属性)
            elif seven_contents:
                for sc in seven_contents:
                    raw = sc.get("8", "")
                    attrs = sc.get("9")
                    if raw and attrs:
                        raw = self._convert_text_attribute(raw, attrs)
                    all_text += raw

            # C. 嵌套结构递归提取（遍历所有子项，杜绝截断）
            elif sub_five:
                all_text += self._parse_rich_inline_content(item)

        return all_text

    def _get_common_text(self, content: dict) -> str:
        """获取普通行内文本（现已完整支持颜色、底色、超链接与样式）"""
        return self._parse_rich_inline_content(content)

    def convert_text_func(self, content) -> str:
        """正常段落文本"""
        return self._parse_rich_inline_content(content)

    def convert_h_func(self, content) -> str:
        """标题"""
        type_name = content.get("4", {}).get("l", "h1")
        text = self._parse_rich_inline_content(content)
        if text and type_name:
            level_str = type_name.replace("h", "")
            try:
                level = int(level_str)
            except Exception:
                level = 1
            return f"{'#' * level} {text}"
        return text

    def convert_im_func(self, content):
        """图片"""
        image_url = content.get("4", {}).get("u", "")
        return f"![]({image_url})"

    def convert_a_func(self, content):
        """附件"""
        fn = content.get("4", {}).get("fn", "")
        fl = content.get("4", {}).get("re", "")
        return f"[{fn}]({fl})"

    def convert_cd_func(self, content):
        """代码块"""
        language = content.get("4", {}).get("la", "")
        codes: list = content.get("5", [])
        code_block = ""
        for code in codes:
            text = self._get_common_text(code)
            code_block += text + "\n"

        return f"```{language}\r\n{code_block}```"

    def convert_la_func(self, content):
        """高亮块"""
        lines: list = content.get("5", [])
        highlight_block = ""
        for line in lines:
            text = self._get_common_text(line)
            highlight_block += text + "\n"

        return f"```\r\n{highlight_block}```"

    def convert_q_func(self, content):
        """引用（支持内部带样式与链接）"""
        q_text_list = content.get("5", [])
        text = ""
        for q_text_dict in q_text_list:
            q_text = self._parse_rich_inline_content(q_text_dict)
            q_text = q_text.replace("\n", "")
            text += f"> {q_text}\n"
        return text

    def convert_l_func(self, content):
        """有序列表和无序列表（支持列表内部颜色与超链接）"""
        text = self._parse_rich_inline_content(content)
        is_ordered = content.get("4", {}).get("lt", "unordered")
        level = content.get("4", {}).get("ll", 1)
        indent = "\t" * max(0, level - 1)
        if is_ordered == "unordered":
            return f"{indent}- {text}"
        else:
            return f"{indent}1. {text}"

    def convert_t_func(self, content):
        """表格转换（支持单元格内部颜色与超链接）"""
        nl = "\r\n"
        tr_list = content.get("5", [])
        table_lines = ""

        for index, tc in enumerate(tr_list):
            table_content_list = tc.get("5", [])
            table_content_len = len(table_content_list)
            if index == 1:
                table_line = "| -- " * table_content_len + "|\n| "
            else:
                table_line = "| "
            for table_content in table_content_list:
                table_text = self._parse_rich_inline_content(table_content)
                if not table_text.strip():
                    table_text = " "
                table_line = table_line + table_text + " | "
            table_lines = table_lines + table_line + nl
        return table_lines


class YoudaoNoteConvert(object):
    """
    有道云笔记 note 内容转换为 markdown 内容
    """

    @staticmethod
    def _insert_timestamps_to_file(file_path, create_time, modify_time):
        """在文件开头插入创建时间和更新时间"""
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        create_time_str = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S")
        modify_time_str = datetime.fromtimestamp(modify_time).strftime("%Y-%m-%d %H:%M:%S")

        timestamp_header = f"创建时间: {create_time_str}\r\n更新时间: {modify_time_str}\r\n\r\n"
        new_content = timestamp_header + content

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    @staticmethod
    def covert_html_to_markdown(file_path, insert_timestamps=False, create_time=None, modify_time=None):
        """转换 HTML 为 Markdown"""
        with open(file_path, "rb") as f:
            content_str = f.read().decode("utf-8")
        from markdownify import markdownify as md

        # 换行预处理
        content_str = content_str.replace("<div>", "\n<div>")
        content_str = content_str.replace("</div>", "</div>\n")
        content_str = content_str.replace("<br>", "<br>\n")
        content_str = content_str.replace("<br/>", "<br/>\n")
        content_str = content_str.replace("<br />", "<br />\n")

        new_content = md(content_str).strip()
        new_content = re.sub(r"\n{3,}", "\n\n", new_content)

        base = os.path.splitext(file_path)[0]
        new_file_path = "".join([base, MARKDOWN_SUFFIX])
        os.rename(file_path, new_file_path)
        with open(new_file_path, "wb") as f:
            f.write(new_content.encode("utf-8"))

        if insert_timestamps and create_time and modify_time:
            YoudaoNoteConvert._insert_timestamps_to_file(new_file_path, create_time, modify_time)

    @staticmethod
    def _covert_xml_to_markdown_content(file_path):
        element_tree = ET.parse(file_path)
        note_element = element_tree.getroot()

        list_item = {}
        for child in note_element[0]:
            if "list" in child.tag:
                list_item[child.attrib.get("id")] = child.attrib.get("type", "unordered")

        body_element = note_element[1]
        new_content_list = []
        for element in list(body_element):
            name = element.tag.replace("{http://note.youdao.com}", "").replace("-", "_")

            # 优先使用富文本连续流式解析器，确保颜色标签后的文字不被截断丢弃
            text = XmlElementConvert.parse_element_rich_text(element)

            convert_func = getattr(
                XmlElementConvert, "convert_{}_func".format(name), None
            )
            if not convert_func:
                if text:
                    new_content_list.append(text)
                continue

            line_content = convert_func(text=text, element=element, list_item=list_item)
            if line_content:
                new_content_list.append(line_content)

        return "\r\n\r\n".join(new_content_list)

    @staticmethod
    def covert_xml_to_markdown(file_path, insert_timestamps=False, create_time=None, modify_time=None) -> bool:
        """转换 XML 为 Markdown"""
        base = os.path.splitext(file_path)[0]
        new_file_path = "".join([base, MARKDOWN_SUFFIX])
        if os.path.getsize(file_path) == 0:
            os.rename(file_path, new_file_path)
            return False

        new_content = YoudaoNoteConvert._covert_xml_to_markdown_content(file_path)
        os.rename(file_path, new_file_path)
        with open(new_file_path, "wb") as f:
            f.write(new_content.encode("utf-8"))

        if insert_timestamps and create_time and modify_time:
            YoudaoNoteConvert._insert_timestamps_to_file(new_file_path, create_time, modify_time)

        return True

    @staticmethod
    def _covert_json_to_markdown_content(file_path):
        new_content_list = []
        with open(file_path, "r", encoding="utf-8") as f:
            try:
                json_data = json.load(f)
            except Exception as e:
                logging.error(e)
                json_data = {}

        json_contents = json_data.get("5", [])
        converter = JsonConvert()
        for content in json_contents:
            item_type = content.get("6")
            if item_type:
                convert_func = getattr(converter, f"convert_{item_type}_func", None)
                if not convert_func:
                    line_content = converter.convert_text_func(content)
                else:
                    line_content = convert_func(content)
            else:
                line_content = converter.convert_text_func(content)

            if line_content:
                new_content_list.append(line_content)

        return "\r\n\r\n".join(new_content_list)

    @staticmethod
    def covert_json_to_markdown(file_path, insert_timestamps=False, create_time=None, modify_time=None) -> str:
        """转换 JSON 为 Markdown"""
        base = os.path.splitext(file_path)[0]
        new_file_path = "".join([base, MARKDOWN_SUFFIX])
        if os.path.getsize(file_path) == 0:
            os.rename(file_path, new_file_path)
            return False

        new_content = YoudaoNoteConvert._covert_json_to_markdown_content(file_path)
        with open(new_file_path, "wb") as f:
            f.write(new_content.encode("utf-8"))

        if os.path.exists(file_path):
            os.remove(file_path)

        if insert_timestamps and create_time and modify_time:
            YoudaoNoteConvert._insert_timestamps_to_file(new_file_path, create_time, modify_time)

        return new_file_path
