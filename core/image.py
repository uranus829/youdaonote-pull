import logging
import os
import re
from typing import Tuple
from urllib import parse
from urllib.parse import urlparse

import requests

REGEX_IMAGE_URL = re.compile(r"!\[.*?\]\((.*?note\.youdao\.com.*?)\)")
REGEX_ATTACH = re.compile(r"\[(.*?)\]\(((http|https)://note\.youdao\.com.*?)\)")
# 有道云笔记的图片地址
IMAGES = "images"
# 有道云笔记的附件地址
ATTACH = "attachments"


class ImagePull:
    def __init__(
        self,
        youdaonote_api,
        smms_secret_token: str,
        is_relative_path: bool,
    ):
        self.youdaonote_api = youdaonote_api
        self.smms_secret_token = smms_secret_token
        self.is_relative_path = is_relative_path

    @classmethod
    def _url_encode(cls, file_path: str):
        """对一些特殊字符 url 编码
        :param file_path:
        """
        file_path = file_path.replace(" ", "%20")
        return file_path

    def migration_ydnote_url(self, file_path):
        """
        迁移有道云笔记文件 URL（图片与附件）
        :param file_path:
        :return:
        """

        # 文件内容为空，也下载到本地
        with open(file_path, "rb") as f:
            content = f.read().decode("utf-8")

        # 1. 迁移图片
        image_urls = REGEX_IMAGE_URL.findall(content)
        if len(image_urls) > 0:
            logging.info("正在转换有道云笔记「{}」中的有道云图片链接...".format(file_path))
        for image_url in image_urls:
            try:
                image_path = self._get_new_image_path(file_path, image_url)
            except Exception as error:
                logging.info(
                    "下载图片「{}」可能失败！请检查图片！错误提示：{}".format(image_url, format(error))
                )
                image_path = image_url
                
            if image_url == image_path:
                continue

            # 将绝对路径替换为相对路径，满足 Obsidian 格式要求
            if self.is_relative_path and not self.smms_secret_token:
                image_path = image_path[image_path.find(IMAGES) :]

            image_path = self._url_encode(image_path)
            content = content.replace(image_url, image_path)

        # 2. 迁移附件
        attach_name_and_url_list = REGEX_ATTACH.findall(content)
        if len(attach_name_and_url_list) > 0:
            logging.info("正在转换有道云笔记「{}」中的有道云附件链接...".format(file_path))
        for attach_name_and_url in attach_name_and_url_list:
            attach_url = attach_name_and_url[1]
            attach_name = attach_name_and_url[0]
            attach_path = self._download_ydnote_url(file_path, attach_url, attach_name)
            if not attach_path:
                continue

            # 将绝对路径转换为相对路径
            if self.is_relative_path:
                attach_path = attach_path[attach_path.find(ATTACH) :]

            attach_path = self._url_encode(attach_path)
            content = content.replace(attach_url, attach_path)

        with open(file_path, "wb") as f:
            f.write(content.encode())
        return

    def _get_new_image_path(self, file_path, image_url) -> str:
        """
        将图片链接转换为新的本地/图床链接
        :param file_path:
        :param image_url:
        :return: new_image_path
        """
        # 当 smms_secret_token 为空（不上传到 SM.MS），直接下载图片到本地
        if not self.smms_secret_token:
            image_path = self._download_ydnote_url(file_path, image_url)
            return image_path or image_url

        # smms_secret_token 不为空，上传到 SM.MS 图床
        new_file_url, error_msg = ImageUpload.upload_to_smms(
            youdaonote_api=self.youdaonote_api,
            image_url=image_url,
            smms_secret_token=self.smms_secret_token,
        )
        # 如果上传失败，仍下载到本地兜底
        if not error_msg:
            return new_file_url
        logging.info(error_msg)
        image_path = self._download_ydnote_url(file_path, image_url)
        return image_path or image_url

    def _download_ydnote_url(self, file_path, url, attach_name=None) -> str:
        """
        下载资源文件（图片或附件）到本地，返回本地路径
        :param file_path: 笔记文件本地全路径
        :param url: 资源下载 URL
        :param attach_name: 附件在 Markdown 中的原名称（图片时通常为 None）
        :return: local_file_path 本地文件绝对路径
        """
        try:
            response = self.youdaonote_api.http_get(url)
        except requests.exceptions.ProxyError as err:
            error_msg = "网络错误，「{}」下载失败。错误提示：{}".format(url, format(err))
            logging.info(error_msg)
            return ""
        except Exception as err:
            error_msg = "请求异常，「{}」下载失败。错误提示：{}".format(url, format(err))
            logging.info(error_msg)
            return ""

        content_type = response.headers.get("Content-Type", "")
        file_type = "附件" if attach_name is not None else "图片"
        if response.status_code != 200 or not content_type:
            error_msg = "下载「{}」失败！{}可能已失效，可浏览器登录有道云笔记后，查看{}是否能正常加载".format(
                url, file_type, file_type
            )
            logging.info(error_msg)
            return ""

        # 确定存储文件夹（图片入 images，附件入 attachments）
        file_dirname = ATTACH if attach_name is not None else IMAGES

        # 确定本地文件夹路径
        if file_path.find(".") == -1:
            local_file_dir = os.path.join(
                getattr(self, "root_local_dir", os.path.dirname(file_path)), file_dirname
            ).replace("\\", "/")
        else:
            note_parent_dir = os.path.dirname(file_path)
            local_file_dir = os.path.join(note_parent_dir, file_dirname).replace("\\", "/")

        if not os.path.exists(local_file_dir):
            os.makedirs(local_file_dir, exist_ok=True)

        # 提取真实文件名
        real_url_obj = urlparse(response.url)
        real_query = parse.parse_qs(real_url_obj.query)

        # 尝试从服务器重定向的 URL 参数获取真实文件名，并进行 URL 解码
        server_filename = ""
        if real_query:
            raw_fn = real_query.get("filename", [""])[0] or real_query.get("download", [""])[0]
            if raw_fn:
                server_filename = parse.unquote(raw_fn)

        if attach_name and attach_name.strip():
            # 策略 1：正文中有明确附件名，直接使用
            file_name = attach_name.strip()
        elif server_filename:
            # 策略 2：正文中无名称（如 XML 中直接粘贴的文件），使用服务器重定向的真实名称
            file_name = server_filename
        else:
            # 策略 3：图片或无名资源兜底推断
            content_type_arr = content_type.split("/")
            file_suffix = (
                "." + content_type_arr[1].replace(";", "")
                if len(content_type_arr) == 2
                else ".jpg"
            )
            file_basename = os.path.basename(urlparse(url).path)
            if not file_basename or file_basename in ["sync", "download"]:
                import uuid
                file_basename = uuid.uuid4().hex
            
            # 若 basename 自身无后缀则补充后缀
            if not os.path.splitext(file_basename)[1]:
                file_name = file_basename + file_suffix
            else:
                file_name = file_basename

        # 清洗文件名中的操作系统非法字符（\ / : * ? " < > |）
        file_name = re.sub(r'[\\/:*?"<>|]', '_', file_name)

        # 避免同名文件相互覆盖（如多个 clipboard.png）
        base_stem, ext = os.path.splitext(file_name)
        counter = 1
        local_file_path = os.path.join(local_file_dir, file_name).replace("\\", "/")
        while os.path.exists(local_file_path):
            file_name = f"{base_stem}_{counter}{ext}"
            local_file_path = os.path.join(local_file_dir, file_name).replace("\\", "/")
            counter += 1

        # 保存文件内容
        try:
            with open(local_file_path, "wb") as f:
                f.write(response.content)
            logging.info("已将{}「{}」转换为「{}」".format(file_type, url, local_file_path))
        except Exception as e:
            error_msg = "{}「{}」保存失败：{}".format(file_type, url, str(e))
            logging.info(error_msg)
            return ""

        return local_file_path

    def _set_relative_file_path(self, file_path, file_name, local_file_dir) -> str:
        """
        图片/附件设置为相对地址
        :param file_path:
        :param file_name:
        :param local_file_dir:
        :return:
        """
        note_file_dir = os.path.dirname(file_path)
        rel_file_dir = os.path.relpath(local_file_dir, note_file_dir)
        rel_file_path = os.path.join(rel_file_dir, file_name)
        new_file_path = rel_file_path.replace("\\", "/")
        return new_file_path


class ImageUpload(object):
    """
    图片上传到指定图床（如 SM.MS）
    """

    @staticmethod
    def upload_to_smms(youdaonote_api, image_url, smms_secret_token) -> Tuple[str, str]:
        """
        上传图片到 sm.ms
        :param youdaonote_api:
        :param image_url:
        :param smms_secret_token:
        :return: url, error_msg
        """
        try:
            smfile = youdaonote_api.http_get(image_url).content
        except Exception:
            error_msg = "下载「{}」失败！图片可能已失效，可浏览器登录有道云笔记后，查看图片是否能正常加载".format(image_url)
            return "", error_msg

        files = {"smfile": smfile}
        upload_api_url = "https://sm.ms/api/v2/upload"
        headers = {"Authorization": smms_secret_token}

        error_msg = (
            "SM.MS 免费版每分钟限额 20 张图片，每小时限额 100 张图片，大小限制 5 M，上传失败！「{}」未转换，"
            "将下载图片到本地".format(image_url)
        )
        try:
            res_json = requests.post(
                upload_api_url, headers=headers, files=files, timeout=5
            ).json()
        except requests.exceptions.ProxyError as err:
            error_msg = "网络错误，上传「{}」到 SM.MS 失败！将下载图片到本地。错误提示：{}".format(
                image_url, format(err)
            )
            return "", error_msg
        except Exception:
            return "", error_msg

        if res_json.get("success"):
            url = res_json["data"]["url"]
            logging.info("已将图片「{}」转换为「{}」".format(image_url, url))
            return url, ""
        if res_json.get("code") == "image_repeated":
            url = res_json["images"]
            logging.info("已将图片「{}」转换为「{}」".format(image_url, url))
            return url, ""
        if res_json.get("code") == "flood":
            return "", error_msg

        error_msg = (
            "上传「{}」到 SM.MS 失败，请检查图片 url 或 smms_secret_token（{}）是否正确！将下载图片到本地".format(
                image_url, smms_secret_token
            )
        )
        return "", error_msg
