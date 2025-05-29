# util/ProxyProvider.py
import requests
import time
from loguru import logger
from concurrent.futures import ThreadPoolExecutor, as_completed

def ping_proxy(proxy, test_url = "https://www.bilibili.com", timeout = 3.0):
    try:
        proxies = {"http": proxy, "https": proxy}  # 同时支持 HTTP 和 HTTPS 代理
        connect_timeout = timeout * 0.5  # 设置连接超时为总超时的一半
        read_timeout = timeout * 0.5  # 设置读取超时为总超时的一半
        
        start = time.time()  # 记录请求开始时间
        resp = requests.get(test_url, proxies=proxies, timeout=(connect_timeout, read_timeout))
        elapsed = time.time() - start  # 计算响应时间

        if resp.status_code == 200:  # 仅在返回 200 状态码时才认为可达
            logger.info(f"代理 {proxy} 可用，响应时间：{elapsed:.2f}s")
            return elapsed
        else:
            logger.warning(f"代理 {proxy} 响应状态码: {resp.status_code}")
    except requests.exceptions.Timeout as e:
        logger.error(f"代理 {proxy} 超时错误: {e}")
    except requests.exceptions.RequestException as e:
        logger.error(f"代理 {proxy} 请求失败: {e}")
    except Exception as e:
        logger.error(f"代理 {proxy} 错误: {e}")
    
    return float("inf")  # 如果请求失败，返回无效时间

def get_proxies_from_kuaidaili(
    signature,
    secret_id,
    username,
    password,
    num,
    batch_size = 10,
    max_attempts = 1000,
    max_workers = 10,
    max_timeout = 3.0
) -> list[str]:
    url_template = (
        "https://dps.kdlapi.com/api/getdps/"
        "?secret_id={secret_id}&signature={signature}&num={batch}&format=text&sep=1"
    )

    usable_proxies = []
    attempts = 0

    while len(usable_proxies) < num and attempts < max_attempts:
        url = url_template.format(secret_id=secret_id, signature=signature, batch=batch_size)
        logger.info(f"尝试第 {attempts + 1} 次拉取代理: {url}")

        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            raw_proxies = resp.text.strip().split("\r\n")

            full_proxies = [f"http://{username}:{password}@{raw}" for raw in raw_proxies]

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_proxy = {executor.submit(ping_proxy, proxy, "https://www.bilibili.com", max_timeout): [proxy, max_timeout] for proxy in full_proxies}
                logger.info(f"正在测试 {len(full_proxies)} 个代理，最大超时设置为 {max_timeout} 秒")
                for future in as_completed(future_to_proxy):
                    input = future_to_proxy[future]
                    proxy = input[0]
                    try:
                        result = future.result()
                        if result != float("inf"):
                            usable_proxies.append(proxy)
                            logger.info(f"第 {len(usable_proxies)}/{num} 个可用代理")
                            if len(usable_proxies) >= num:
                                break
                    except Exception as e:
                        logger.error(f"代理 {proxy} 测试线程异常: {e}")

        except Exception as e:
            logger.error(f"拉取代理失败: {e}")

        attempts += 1
        if len(usable_proxies) < num:
            time.sleep(1)  # 避免被限频

    if len(usable_proxies) < num:
        logger.warning(f"仅获取到 {len(usable_proxies)} 个可用代理，目标为 {num}")
    else:
        logger.info(f"成功获取 {len(usable_proxies)} 个可用代理")

    return usable_proxies


def filter_and_rank_proxies(proxy_list: list[str], max_rtt: float = 5.0) -> list[str]:
    results = []
    for proxy in proxy_list:
        if proxy.lower() == "none":
            continue
        rtt = ping_proxy(proxy)
        if rtt != float("inf") and rtt <= max_rtt:
            logger.info(f"{proxy} 响应时间: {rtt:.2f}s")
            results.append((proxy, rtt))
        else:
            logger.warning(f"{proxy} 不可用或超时")

    # 按 rtt 升序排序
    results.sort(key=lambda x: x[1])
    return [proxy for proxy, _ in results]
