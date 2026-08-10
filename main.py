from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.spinner import Spinner
from kivy.uix.image import AsyncImage
from kivy.uix.popup import Popup
from kivy.uix.filechooser import FileChooserListView
from kivy.clock import Clock
import threading
import requests
import json
import tempfile
import os
from io import BytesIO
from PIL import Image
import time

CREATE_API_URL = "https://api.wuyinkeji.com/api/async/image_gpt"
DETAIL_API_URL = "https://api.wuyinkeji.com/api/async/detail"
IMAGE_BED_API_URL = "https://imgur.la/api/1/upload"

class MainLayout(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', padding=10, spacing=10, **kwargs)
        self.add_widget(Label(text='生成 API Key:', size_hint_y=None, height=30))
        self.api_key = TextInput(multiline=False, password=True)
        self.add_widget(self.api_key)

        self.add_widget(Label(text='图床 API Key:', size_hint_y=None, height=30))
        self.imgur_key = TextInput(multiline=False, password=True)
        self.add_widget(self.imgur_key)

        self.add_widget(Label(text='提示词:', size_hint_y=None, height=30))
        self.prompt = TextInput(multiline=True, size_hint_y=None, height=80)
        self.add_widget(self.prompt)

        self.add_widget(Label(text='比例:', size_hint_y=None, height=30))
        self.size_spinner = Spinner(text='auto', values=['auto','1:1','16:9','9:16','3:2','2:3'], size_hint_y=None, height=44)
        self.add_widget(self.size_spinner)

        self.add_widget(Label(text='生成数量:', size_hint_y=None, height=30))
        self.count_spinner = Spinner(text='1', values=['1','2','3','4'], size_hint_y=None, height=44)
        self.add_widget(self.count_spinner)

        self.img_label = Label(text='未选择图片', size_hint_y=None, height=30)
        self.add_widget(self.img_label)
        btn_select = Button(text='选择图片（可选）', size_hint_y=None, height=50)
        btn_select.bind(on_press=self.select_image)
        self.add_widget(btn_select)

        btn_start = Button(text='开始生成', size_hint_y=None, height=50, background_color=(0.2,0.6,1,1))
        btn_start.bind(on_press=self.start_generate)
        self.add_widget(btn_start)

        self.status = Label(text='等待操作', size_hint_y=None, height=30)
        self.add_widget(self.status)

        self.result_box = BoxLayout(orientation='vertical', size_hint_y=None)
        self.result_box.bind(minimum_height=self.result_box.setter('height'))
        scroll = ScrollView(size_hint=(1,1))
        scroll.add_widget(self.result_box)
        self.add_widget(scroll)

        self.image_path = None

    def select_image(self, instance):
        content = FileChooserListView(filters=['*.png','*.jpg','*.jpeg','*.gif','*.bmp'])
        popup = Popup(title="选择图片", content=content, size_hint=(0.9,0.9))
        content.bind(on_submit=lambda *args: self._file_selected(args[1], popup))
        popup.open()

    def _file_selected(self, selection, popup):
        if selection:
            self.image_path = selection[0]
            self.img_label.text = f'已选择: {os.path.basename(self.image_path)}'
        popup.dismiss()

    def start_generate(self, instance):
        api = self.api_key.text.strip()
        imgur = self.imgur_key.text.strip()
        prompt = self.prompt.text.strip()
        if not api or not imgur or not prompt:
            self.status.text = '请填写 Key 和提示词'
            return
        self.status.text = '正在处理...'
        threading.Thread(target=self._run_task, args=(api, imgur, prompt), daemon=True).start()

    def _run_task(self, api, imgur, prompt):
        try:
            image_urls = []
            if self.image_path:
                Clock.schedule_once(lambda dt: setattr(self, 'status.text', '压缩并上传图片...'))
                compressed, mime = self._compress_image(self.image_path)
                url = self._upload_image(compressed, imgur, mime)
                os.unlink(compressed)
                image_urls.append(url)

            Clock.schedule_once(lambda dt: setattr(self, 'status.text', '创建生成任务...'))
            headers = {"Authorization": api, "Content-Type": "application/json"}
            payload = {"prompt": prompt, "size": self.size_spinner.text, "count": int(self.count_spinner.text)}
            if image_urls:
                payload["urls"] = image_urls
            resp = requests.post(CREATE_API_URL, headers=headers, json=payload, timeout=30)
            data = resp.json()
            if data.get("code") != 200:
                raise Exception(f"创建任务失败: {data}")
            task_id = data["data"]["id"]

            for i in range(60):
                Clock.schedule_once(lambda dt, p=i: setattr(self, 'status.text', f'轮询中... ({p+1}/60)'))
                time.sleep(5)
                resp = requests.get(DETAIL_API_URL, headers={"Authorization": api}, params={"id": task_id})
                res = resp.json()
                if res.get("code") != 200:
                    raise Exception("查询失败")
                status = int(res["data"].get("status", -1))
                if status == 2:
                    urls = self._extract_urls(res)
                    if not urls:
                        raise Exception("未提取到图片链接")
                    Clock.schedule_once(lambda dt: self._display_images(urls))
                    Clock.schedule_once(lambda dt: setattr(self, 'status.text', '生成完成！'))
                    return
                elif status == 3:
                    raise Exception(f"任务失败: {res['data'].get('message','')}")
            raise Exception("轮询超时")
        except Exception as e:
            Clock.schedule_once(lambda dt: setattr(self, 'status.text', f'错误: {str(e)}'))

    def _compress_image(self, path):
        img = Image.open(path)
        if img.width > 1200:
            ratio = 1200 / img.width
            img = img.resize((1200, int(img.height*ratio)), Image.LANCZOS)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        img.save(tmp, format='PNG')
        tmp.close()
        if os.path.getsize(tmp.name) > 5*1024*1024:
            os.unlink(tmp.name)
            if img.mode in ('RGBA','LA','P'):
                img = img.convert('RGB')
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
            img.save(tmp, format='JPEG', quality=80)
            tmp.close()
            return tmp.name, 'image/jpeg'
        return tmp.name, 'image/png'

    def _upload_image(self, path, api_key, mime):
        with open(path, 'rb') as f:
            files = {"source": (os.path.basename(path), f, mime)}
            headers = {"X-API-Key": api_key}
            resp = requests.post(IMAGE_BED_API_URL, headers=headers, files=files, timeout=60)
        if resp.status_code != 200:
            raise Exception(f"图床上传失败: {resp.status_code}")
        data = resp.json()
        img = data.get("image", {})
        url = img.get("url") or img.get("display_url")
        if not url and "file" in img and "resource" in img["file"]:
            url = img["file"]["resource"].get("url")
            if url and url.startswith("/"):
                url = "https://imgur.la" + url
        if not url:
            for k,v in img.items():
                if isinstance(v,str) and k.endswith("url") and v.startswith("http"):
                    url = v
                    break
        if not url:
            raise Exception("无法提取图床链接")
        return url

    def _extract_urls(self, res):
        urls = []
        data = res.get("data", {})
        possible = ["url","image_url","result_url","src"]
        for key in possible:
            val = data.get(key)
            if isinstance(val,str) and val.startswith("http") and val not in urls:
                urls.append(val)
        if "urls" in data and isinstance(data["urls"], list):
            for item in data["urls"]:
                if isinstance(item,str) and item.startswith("http") and item not in urls:
                    urls.append(item)
                elif isinstance(item,dict):
                    for key in possible:
                        v = item.get(key)
                        if isinstance(v,str) and v.startswith("http") and v not in urls:
                            urls.append(v)
        if "images" in data and isinstance(data["images"], list):
            for item in data["images"]:
                if isinstance(item,str) and item.startswith("http") and item not in urls:
                    urls.append(item)
                elif isinstance(item,dict):
                    for key in possible:
                        v = item.get(key)
                        if isinstance(v,str) and v.startswith("http") and v not in urls:
                            urls.append(v)
        return urls

    def _display_images(self, urls):
        self.result_box.clear_widgets()
        for url in urls:
            img = AsyncImage(source=url, size_hint_y=None, height=300)
            self.result_box.add_widget(img)
            self.result_box.add_widget(Label(text='', size_hint_y=None, height=5))

class AIApp(App):
    def build(self):
        return MainLayout()

if __name__ == '__main__':
    AIApp().run()
