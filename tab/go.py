import datetime
import importlib
import os
import time
import gradio as gr
from gradio import SelectData
from loguru import logger
import requests

from geetest.Validator import Validator
from task.buy import buy_new_terminal
from util import ConfigDB, Endpoint, GlobalStatusInstance, time_service
from util import bili_ticket_gt_python
from util.ProxyProvider import get_proxies_from_kuaidaili, filter_and_rank_proxies, ping_proxy

def withTimeString(string):
    return f"{datetime.datetime.now()}: {string}"


ways: list[str] = []
ways_detail: list[Validator] = []
if bili_ticket_gt_python is not None:
    ways_detail.insert(
        0, importlib.import_module("geetest.TripleValidator").TripleValidator()
    )
    ways.insert(0, "本地过验证码v2(Amorter提供)")
    # ways_detail.insert(0, importlib.import_module("geetest.AmorterValidator").AmorterValidator())
    # ways.insert(0, "本地过验证码(Amorter提供)")


def go_tab(demo: gr.Blocks):
    with gr.Column():
        gr.Markdown("""
            ### 上传或填入你要抢票票种的配置信息
            """)
        with gr.Row():
            upload_ui = gr.Files(
                label="上传多个配置文件，点击不同的配置文件可快速切换",
                file_count="multiple",
            )
            ticket_ui = gr.TextArea(label="查看", info="配置信息", interactive=False)
        with gr.Row(variant="compact"):
            gr.HTML(
                """
                    <div class="text-pink-100">
                        程序已经提前帮你校准时间，设置成开票时间即可。请勿设置成开票前的时间。在开票前抢票会短暂封号
                    </div>
                    <input 
                        type="datetime-local" 
                        id="datetime" 
                        name="datetime" 
                        step="1" 
                        class="border border-gray-300 rounded-md p-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                </div>
                """,
                label="选择抢票的时间",
                show_label=True,
            )

        def upload(filepath):
            try:
                with open(filepath[0], "r", encoding="utf-8") as file:
                    content = file.read()
                return content
            except Exception as e:
                return str(e)

        def file_select_handler(select_data: SelectData, files):
            file_label = files[select_data.index]
            try:
                with open(file_label, "r", encoding="utf-8") as file:
                    content = file.read()
                return content
            except Exception as e:
                return str(e)

        upload_ui.upload(fn=upload, inputs=upload_ui, outputs=ticket_ui)
        upload_ui.select(file_select_handler, upload_ui, ticket_ui)

        # 手动设置/更新时间偏差
        with gr.Accordion(label="手动设置/更新时间偏差", open=False):
            time_diff_ui = gr.Number(
                label="当前脚本时间偏差 (单位: ms)",
                info="你可以在这里手动输入时间偏差, 或点击下面按钮自动更新当前时间偏差。正值将推迟相应时间开始抢票, 负值将提前相应时间开始抢票。",
                value=float(format(time_service.get_timeoffset() * 1000, ".2f")),
            )  # type: ignore
            refresh_time_ui = gr.Button(value="点击自动更新时间偏差")
            refresh_time_ui.click(
                fn=lambda: format(
                    float(time_service.compute_timeoffset()) * 1000, ".2f"
                ),
                inputs=None,
                outputs=time_diff_ui,
            )
            time_diff_ui.change(
                fn=lambda x: time_service.set_timeoffset(
                    format(float(x) / 1000, ".5f")
                ),
                inputs=time_diff_ui,
                outputs=None,
            )

        # 验证码选择
        select_way = 0
        way_select_ui = gr.Radio(
            ways,
            label="过验证码的方式",
            info="详细说明请前往 `训练你的验证码速度` 那一栏",
            type="index",
            value=ways[select_way],
        )

        with gr.Accordion(label="填写你的HTTPS代理服务器 [可选]", open=False):
            gr.Markdown("""
            > 可选择 “手动输入” 或 “使用快代理 API 拉取”，程序在风控时将使用这些代理重试请求。
            """)

            # 选择模式
            proxy_mode_radio = gr.Radio(
                ["手动输入", "使用快代理 API 自动拉取"],
                label="代理配置方式",
                value="手动输入",
                interactive=True
            )

            # 表格展示代理状态
            proxy_status_table = gr.Dataframe(
                headers=["代理地址", "状态"],
                datatype=["str", "str"],
                row_count=(5, "dynamic"),
                col_count=(2, "fixed"),
                label="代理可达性列表",
                interactive=False
            )

            # ==== 手动输入 ====
            https_proxy_ui = gr.Textbox(
                label="手动输入代理地址（多个用逗号分隔）",
                info="格式: http://1.1.1.1:8080,http://2.2.2.2:8080",
                visible=True,
                value=ConfigDB.get("https_proxy") or "",
            )

            https_proxy_submit_btn = gr.Button("保存并检测这些代理", visible=True)

            def save_manual_proxy(proxy_str):
                proxies = [p.strip() for p in proxy_str.split(",") if p.strip()]
                if not proxies:
                    return [["❌ 无代理", ""]]

                ConfigDB.insert("https_proxy", proxy_str)
                results = []
                for proxy in proxies:
                    try:
                        rtt = ping_proxy(proxy)
                        if rtt == float("inf"):
                            results.append([proxy, "❌ 不可达"])
                        else:
                            results.append([proxy, f"✅ {rtt:.2f}s"])
                    except Exception:
                        results.append([proxy, "❌ 测试异常"])
                return results

            https_proxy_submit_btn.click(
                fn=save_manual_proxy,
                inputs=https_proxy_ui,
                outputs=proxy_status_table,
                queue=False,
            )

            # ==== 快代理 ====
            with gr.Row():
                kuaidaili_secret_id_ui = gr.Textbox(
                    label="快代理 订单 secret_id",
                    value=ConfigDB.get("kuaidaili_secret_id") or "",
                    visible=False,  # 初始不可见
                )
                kuaidaili_signature_ui = gr.Textbox(
                    label="快代理 订单 signature/secret_key",
                    value=ConfigDB.get("kuaidaili_signature") or "",
                    visible=False,  # 初始不可见
                )
            with gr.Row():
                kuaidaili_username_ui = gr.Textbox(
                    label="快代理 用户名",
                    value=ConfigDB.get("kuaidaili_username") or "",
                    visible=False,  # 初始不可见
                )
                kuaidaili_password_ui = gr.Textbox(
                    label="快代理 密码",
                    value=ConfigDB.get("kuaidaili_password") or "",
                    visible=False,  # 初始不可见
                )
            with gr.Row():
                kuaidaili_num_ui = gr.Number(
                    label="拉取代理数量",
                    value=5,
                    minimum=1,
                    maximum=100,
                    step=1,
                    visible=False,  # 初始不可见
                )
                kuaidaili_max_timeout_ui = gr.Number(
                    label="代理测试最大超时 (秒)",
                    value=5,
                    minimum=1,
                    maximum=30,
                    step=1,
                    visible=False,  # 初始不可见
                )

            def save_secret_id(secret_id):
                ConfigDB.insert("kuaidaili_secret_id", secret_id)
            def save_signature(signature):
                ConfigDB.insert("kuaidaili_signature", signature)
            def save_username(username):
                ConfigDB.insert("kuaidaili_username", username)
            def save_password(password):
                ConfigDB.insert("kuaidaili_password", password)
            def save_kuaidaili_num(num):
                ConfigDB.insert("kuaidaili_num", num)
            def save_kuaidaili_max_timeout(max_timeout):
                ConfigDB.insert("kuaidaili_max_timeout", max_timeout)

            kuaidaili_secret_id_ui.change(fn=save_secret_id, inputs=kuaidaili_secret_id_ui)
            kuaidaili_signature_ui.change(fn=save_signature, inputs=kuaidaili_signature_ui)
            kuaidaili_username_ui.change(fn=save_username, inputs=kuaidaili_username_ui)
            kuaidaili_password_ui.change(fn=save_password, inputs=kuaidaili_password_ui)
            kuaidaili_num_ui.change(fn=save_kuaidaili_num, inputs=kuaidaili_num_ui)
            kuaidaili_max_timeout_ui.change(fn=save_kuaidaili_max_timeout, inputs=kuaidaili_max_timeout_ui)

            refresh_proxy_btn = gr.Button("从快代理拉取并检测", visible=False)  # 初始不可见

            def refresh_proxies_with_status():
                signature = ConfigDB.get("kuaidaili_signature")
                if not signature:
                    return [["❌ 请填写 signature", ""]]
                secret_id = ConfigDB.get("kuaidaili_secret_id")
                if not secret_id:
                    return [["❌ 请填写 secret_id", ""]]
                username = ConfigDB.get("kuaidaili_username")
                if not username:
                    return [["❌ 请填写用户名", ""]]
                password = ConfigDB.get("kuaidaili_password")
                if not password:
                    return [["❌ 请填写密码", ""]]
                num = ConfigDB.get("kuaidaili_num") or 5
                if not isinstance(num, int) or num <= 0:
                    return [["❌ 请填写有效的拉取数量", ""]]
                max_timeout = ConfigDB.get("kuaidaili_max_timeout") or 5
                if not isinstance(max_timeout, (int, float)) or max_timeout <= 0:
                    return [["❌ 请填写有效的最大超时", ""]]
                raw_list = get_proxies_from_kuaidaili(signature, secret_id, username, password, num, max_timeout=max_timeout)
                if not raw_list:
                    return [["❌ 拉取失败", ""]]

                results = []
                valid = []
                logger.info(f"获取到 {len(raw_list)} 个代理")
                logger.info(f"代理列表: {raw_list}")
                for proxy in raw_list:
                    rtt = ping_proxy(proxy)
                    if rtt == float("inf"):
                        results.append([proxy, "❌ 不可达"])
                    else:
                        results.append([proxy, f"✅ {rtt:.2f}s"])
                        valid.append(proxy)

                ConfigDB.insert("https_proxy", ",".join(valid))
                return results

            refresh_proxy_btn.click(
                fn=refresh_proxies_with_status,
                inputs=[],
                outputs=proxy_status_table,
                queue=False,
            )

            # ==== 模式切换逻辑 ====
            def toggle_proxy_mode(mode):
                is_manual = (mode == "手动输入")
                return (
                    gr.update(visible=is_manual),        # https_proxy_ui
                    gr.update(visible=is_manual),        # submit_btn
                    gr.update(visible=not is_manual),    # kuaidaili_secret_id_ui
                    gr.update(visible=not is_manual),    # kuaidaili_signature_ui
                    gr.update(visible=not is_manual),    # kuaidaili_username_ui
                    gr.update(visible=not is_manual),    # kuaidaili_password_ui
                    gr.update(visible=not is_manual),    # kuaidaili_num_ui
                    gr.update(visible=not is_manual),    # kuaidaili_max_timeout_ui
                    gr.update(visible=not is_manual),    # refresh btn
                )

            proxy_mode_radio.change(
                fn=toggle_proxy_mode,
                inputs=proxy_mode_radio,
                outputs=[
                    https_proxy_ui,
                    https_proxy_submit_btn,
                    kuaidaili_secret_id_ui,
                    kuaidaili_signature_ui,
                    kuaidaili_username_ui,
                    kuaidaili_password_ui,
                    kuaidaili_num_ui,
                    kuaidaili_max_timeout_ui,
                    refresh_proxy_btn
                ],
                queue=False,
            )


        with gr.Accordion(label="配置抢票声音提醒[可选]", open=False):
            with gr.Row():
                audio_path_ui = gr.Audio(
                    label="上传提示声音[只支持格式wav]", type="filepath", loop=True
                )
        with gr.Accordion(label="配置抢票消息提醒[可选]", open=False):
            gr.Markdown(
                """
                🗨️ 抢票成功提醒
                > 你需要去对应的网站获取key或token，然后填入下面的输入框
                > [Server酱](https://sct.ftqq.com/sendkey) | [pushplus](https://www.pushplus.plus/uc.html) | [ntfy](https://ntfy.sh/)
                > 留空以不启用提醒功能
                """
            )
            with gr.Row():
                serverchan_ui = gr.Textbox(
                    value=ConfigDB.get("serverchanKey")
                    if ConfigDB.get("serverchanKey") is not None
                    else "",
                    label="Server酱的SendKey",
                    interactive=True,
                    info="https://sct.ftqq.com/",
                )

                pushplus_ui = gr.Textbox(
                    value=ConfigDB.get("pushplusToken")
                    if ConfigDB.get("pushplusToken") is not None
                    else "",
                    label="PushPlus的Token",
                    interactive=True,
                    info="https://www.pushplus.plus/",
                )

                ntfy_ui = gr.Textbox(
                    value=ConfigDB.get("ntfyUrl")
                    if ConfigDB.get("ntfyUrl") is not None
                    else "",
                    label="Ntfy服务器URL",
                    interactive=True,
                    info="例如: https://ntfy.sh/your-topic",
                )

                with gr.Accordion(label="Ntfy认证配置[可选]", open=False):
                    with gr.Row():
                        ntfy_username_ui = gr.Textbox(
                            value=ConfigDB.get("ntfyUsername")
                            if ConfigDB.get("ntfyUsername") is not None
                            else "",
                            label="Ntfy用户名",
                            interactive=True,
                            info="如果你的Ntfy服务器需要认证",
                        )

                        ntfy_password_ui = gr.Textbox(
                            value=ConfigDB.get("ntfyPassword")
                            if ConfigDB.get("ntfyPassword") is not None
                            else "",
                            label="Ntfy密码",
                            interactive=True,
                            type="password"
                        )

                    def test_ntfy_connection():
                        url = ConfigDB.get("ntfyUrl")
                        username = ConfigDB.get("ntfyUsername")
                        password = ConfigDB.get("ntfyPassword")

                        if not url:
                            return "错误: 请先设置Ntfy服务器URL"

                        from util import NtfyUtil
                        success, message = NtfyUtil.test_connection(url, username, password)

                        if success:
                            return f"成功: {message}"
                        else:
                            return f"错误: {message}"

                    test_ntfy_button = gr.Button("测试Ntfy连接")
                    test_ntfy_result = gr.Textbox(label="测试结果", interactive=False)
                    test_ntfy_button.click(fn=test_ntfy_connection, inputs=[], outputs=test_ntfy_result)

                def inner_input_serverchan(x):
                    return ConfigDB.insert("serverchanKey", x)

                def inner_input_pushplus(x):
                    return ConfigDB.insert("pushplusToken", x)

                def inner_input_ntfy(x):
                    return ConfigDB.insert("ntfyUrl", x)

                def inner_input_ntfy_username(x):
                    return ConfigDB.insert("ntfyUsername", x)

                def inner_input_ntfy_password(x):
                    return ConfigDB.insert("ntfyPassword", x)

                serverchan_ui.change(fn=inner_input_serverchan, inputs=serverchan_ui)

                pushplus_ui.change(fn=inner_input_pushplus, inputs=pushplus_ui)

                ntfy_ui.change(fn=inner_input_ntfy, inputs=ntfy_ui)

                ntfy_username_ui.change(fn=inner_input_ntfy_username, inputs=ntfy_username_ui)

                ntfy_password_ui.change(fn=inner_input_ntfy_password, inputs=ntfy_password_ui)

        def choose_option(way):
            nonlocal select_way
            select_way = way

        way_select_ui.change(choose_option, inputs=way_select_ui)

        with gr.Row():
            interval_ui = gr.Number(
                label="抢票间隔",
                value=300,
                minimum=1,
                info="设置抢票任务之间的时间间隔（单位：毫秒），建议不要设置太小",
            )
            mode_ui = gr.Radio(
                label="抢票次数",
                choices=["无限", "有限"],
                value="无限",
                info="选择抢票的次数",
                type="index",
                interactive=True,
            )
            total_attempts_ui = gr.Number(
                label="总过次数",
                value=100,
                minimum=1,
                info="设置抢票的总次数",
                visible=False,
            )

    def try_assign_endpoint(endpoint_url, payload):
        try:
            response = requests.post(f"{endpoint_url}/buy", json=payload, timeout=5)
            if response.status_code == 200:
                return True
            elif response.status_code == 409:
                logger.info(f"{endpoint_url} 已经占用")
                return False
            else:
                return False

        except Exception as e:
            logger.exception(e)
            raise e

    def split_proxies(https_proxy_list: list[str], task_num: int) -> list[list[str]]:
        assigned_proxies: list[list[str]] = [[] for _ in range(task_num)]
        for i, proxy in enumerate(https_proxy_list):
            assigned_proxies[i % task_num].append(proxy)
        return assigned_proxies

    def start_go(
        files, time_start, interval, mode, total_attempts, audio_path, https_proxys
    ):
        if not files:
            return [gr.update(value=withTimeString("未提交抢票配置"), visible=True)]
        yield [
            gr.update(value=withTimeString("开始多开抢票,详细查看终端"), visible=True)
        ]
        endpoints = GlobalStatusInstance.available_endpoints()
        endpoints_next_idx = 0
        https_proxy_list = ["none"] + https_proxys.split(",")
        assigned_proxies: list[list[str]] = []
        assigned_proxies_next_idx = 0
        for idx, filename in enumerate(files):
            with open(filename, "r", encoding="utf-8") as file:
                content = file.read()
            filename_only = os.path.basename(filename)
            logger.info(f"启动 {filename_only}")
            # 先分配worker
            while endpoints_next_idx < len(endpoints):
                success = try_assign_endpoint(
                    endpoints[endpoints_next_idx].endpoint,
                    payload={
                        "force": True,
                        "train_info": content,
                        "time_start": time_start,
                        "interval": interval,
                        "mode": mode,
                        "total_attempts": total_attempts,
                        "audio_path": audio_path,
                        "pushplusToken": ConfigDB.get("pushplusToken"),
                        "serverchanKey": ConfigDB.get("serverchanKey"),
                        "ntfy_url": ConfigDB.get("ntfyUrl"),
                        "ntfy_username": ConfigDB.get("ntfyUsername"),
                        "ntfy_password": ConfigDB.get("ntfyPassword"),
                    },
                )
                endpoints_next_idx += 1
                if success:
                    break
            else:
                # 再分配https_proxys
                if assigned_proxies == []:
                    left_task_num = len(files) - idx
                    assigned_proxies = split_proxies(https_proxy_list, left_task_num)

                buy_new_terminal(
                    endpoint_url=demo.local_url,
                    filename=filename,
                    tickets_info_str=content,
                    time_start=time_start,
                    interval=interval,
                    mode=mode,
                    total_attempts=total_attempts,
                    audio_path=audio_path,
                    pushplusToken=ConfigDB.get("pushplusToken"),
                    serverchanKey=ConfigDB.get("serverchanKey"),
                    ntfy_url=ConfigDB.get("ntfyUrl"),
                    ntfy_username=ConfigDB.get("ntfyUsername"),
                    ntfy_password=ConfigDB.get("ntfyPassword"),
                    https_proxys=",".join(assigned_proxies[assigned_proxies_next_idx]),
                )
                assigned_proxies_next_idx += 1
        gr.Info("正在启动，请等待抢票页面弹出。")

    def start_process(
            files,
            time_start,
            interval,
            mode,
            total_attempts,
            audio_path,
            https_proxys,
            progress=gr.Progress(),
    ):
        """
        不同start_go，start_process会采取队列的方式抢票，首先他会当前抢票的配置文件，依此进行抢票。

        抢票并发量为： worker数目+ (1+代理数目)/2 向上取整


        """
        if not files:
            return [gr.update(value=withTimeString("未提交抢票配置"), visible=True)]
        yield [
            gr.update(value=withTimeString("开始多开抢票,详细查看终端"), visible=True)
        ]
        endpoints = GlobalStatusInstance.available_endpoints()
        endpoints_next_idx = 0
        https_proxy_list = ["none"] + https_proxys.split(",")
        assigned_proxies: list[list[str]] = []
        assigned_proxies_next_idx = 0
        for idx, filename in enumerate(files):
            with open(filename, "r", encoding="utf-8") as file:
                content = file.read()
            filename_only = os.path.basename(filename)
            logger.info(f"启动 {filename_only}")
            # 先分配worker
            while endpoints_next_idx < len(endpoints):
                success = try_assign_endpoint(
                    endpoints[endpoints_next_idx].endpoint,
                    payload={
                        "force": True,
                        "train_info": content,
                        "time_start": time_start,
                        "interval": interval,
                        "mode": mode,
                        "total_attempts": total_attempts,
                        "audio_path": audio_path,
                        "pushplusToken": ConfigDB.get("pushplusToken"),
                        "serverchanKey": ConfigDB.get("serverchanKey"),
                        "ntfy_url": ConfigDB.get("ntfyUrl"),
                        "ntfy_username": ConfigDB.get("ntfyUsername"),
                        "ntfy_password": ConfigDB.get("ntfyPassword"),
                    },
                )
                endpoints_next_idx += 1
                if success:
                    break
            else:
                # 再分配https_proxys
                if assigned_proxies == []:
                    left_task_num = len(files) - idx
                    assigned_proxies = split_proxies(https_proxy_list, left_task_num)

                buy_new_terminal(
                    endpoint_url=demo.local_url,
                    filename=filename,
                    tickets_info_str=content,
                    time_start=time_start,
                    interval=interval,
                    mode=mode,
                    total_attempts=total_attempts,
                    audio_path=audio_path,
                    pushplusToken=ConfigDB.get("pushplusToken"),
                    serverchanKey=ConfigDB.get("serverchanKey"),
                    ntfy_url=ConfigDB.get("ntfyUrl"),
                    ntfy_username=ConfigDB.get("ntfyUsername"),
                    ntfy_password=ConfigDB.get("ntfyPassword"),
                    https_proxys=",".join(assigned_proxies[assigned_proxies_next_idx]),
                )
                assigned_proxies_next_idx += 1
        gr.Info("正在启动，请等待抢票页面弹出。")

    mode_ui.change(
        fn=lambda x: gr.update(visible=True) if x == 1 else gr.update(visible=False),
        inputs=[mode_ui],
        outputs=total_attempts_ui,
    )

    go_btn = gr.Button("开始抢票")
    process_btn = gr.Button("开始蹲票")

    _time_tmp = gr.Textbox(visible=False)
    go_btn.click(
        fn=None,
        inputs=None,
        outputs=_time_tmp,
        js='(x) => document.getElementById("datetime").value',
    )
    _report_tmp = gr.Button(visible=False)
    _report_tmp.api_info

    # hander endpoint hearts

    _end_point_tinput = gr.Textbox(visible=False)

    def report(end_point, detail):
        now = time.time()
        GlobalStatusInstance.endpoint_details[end_point] = Endpoint(
            endpoint=end_point, detail=detail, update_at=now
        )

    _report_tmp.click(
        fn=report,
        inputs=[_end_point_tinput, _time_tmp],  # fake useage
        api_name="report",
    )

    def tick():
        return f"当前时间戳：{int(time.time())}"

    timer = gr.Textbox(label="定时更新", interactive=False, visible=False)
    demo.load(fn=tick, inputs=None, outputs=timer, every=1)

    @gr.render(inputs=timer)
    def show_split(text):
        endpoints = GlobalStatusInstance.available_endpoints()
        if len(endpoints) == 0:
            gr.Markdown("## 无运行终端")
        else:
            gr.Markdown("## 当前运行终端列表")
            for endpoint in endpoints:
                with gr.Row():
                    gr.Button(
                        value=f"点击跳转 🚀 {endpoint.endpoint} {endpoint.detail}",
                        link=endpoint.endpoint,
                    )

    go_btn.click(
        fn=start_go,
        inputs=[
            upload_ui,
            _time_tmp,
            interval_ui,
            mode_ui,
            total_attempts_ui,
            audio_path_ui,
            https_proxy_ui,
        ],
    )
    process_btn.click(
        fn=start_process,
        inputs=[
            upload_ui,
            _time_tmp,
            interval_ui,
            mode_ui,
            total_attempts_ui,
            audio_path_ui,
            https_proxy_ui,
        ],
        outputs=process_btn,
    )
