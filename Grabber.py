# server.py
# -*- coding: utf-8 -*-
# pip install fastapi uvicorn playwright
# python -m playwright install chromium

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Dict
from fastapi import FastAPI, Body, Request
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright

# ======== 固定参数（按需修改） ========
OUTPUT_DIR = r"C:\Users\admin\Downloads"  # ✅ 固定保存目录
HEADLESS = True
TIMEOUT_MS = 6_000
VIEWPORT = (2560, 1440)
DEVICE_SCALE_FACTOR = 3
PAGE_ZOOM = 1.0

# 你给的 XPath
X_INPUT  = "/html/body/div[2]/div[2]/div[2]/form/div/input"
X_BTN    = "/html/body/div[2]/div[2]/div[2]/form/div/button"
X_CLICK1 = "/html/body/table/tbody/tr/td[2]/div/table[2]/tbody[1]/tr[1]/td[4]"
X_SHOT   = "/html/body/table/tbody/tr/td[2]/div/table[4]"
# =====================================

def _sanitize_filename(name: str) -> str:
    return "".join(c for c in str(name) if c not in r'\/:*?"<>|').strip()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """使用 FastAPI Lifespan 管理启动/关闭（替代 on_event）。"""
    app.state.playwright = await async_playwright().start()
    app.state.browser = await app.state.playwright.chromium.launch(
        headless=HEADLESS,
        args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
              f"--window-size={VIEWPORT[0]},{VIEWPORT[1]}"]
    )
    app.state.queue_lock = asyncio.Semaphore(1)  # 串行排队
    print("[READY] Browser launched.")
    try:
        yield
    finally:
        await app.state.browser.close()
        await app.state.playwright.stop()
        print("[CLOSED] Browser closed.")

app = FastAPI(title="ProteinAtlas Capture API", lifespan=lifespan)

async def _capture_one_raw(request: Request, code: str, target: str) -> tuple[bool, str]:
    """
    按流程抓取并仅元素截图：
    成功 -> (True, 保存路径)；失败 -> (False, 错误信息)
    """
    code = _sanitize_filename(code)
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{code}.png"

    browser = request.app.state.browser
    context = await browser.new_context(
        locale="zh-CN",
        viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]},
        device_scale_factor=DEVICE_SCALE_FACTOR,
    )
    page = await context.new_page()

    try:
        # 1) 打开网站
        await page.goto("https://www.proteinatlas.org/", wait_until="domcontentloaded", timeout=10_000)
        if PAGE_ZOOM and PAGE_ZOOM != 1.0:
            await page.evaluate("zoom => document.body.style.zoom = String(zoom)", PAGE_ZOOM)

        # 2) 填搜索（靶点名）
        inp = page.locator(f"xpath={X_INPUT}")
        await inp.wait_for(state="visible", timeout=TIMEOUT_MS)
        await inp.fill(str(target))

        # 3) 点击搜索
        btn = page.locator(f"xpath={X_BTN}")
        await btn.wait_for(state="visible", timeout=TIMEOUT_MS)
        await btn.click()
        await page.wait_for_load_state("domcontentloaded")

        # 4) 点击结果位置
        cell = page.locator(f"xpath={X_CLICK1}")
        await cell.wait_for(state="visible", timeout=TIMEOUT_MS)
        await cell.click()
        await page.wait_for_load_state("domcontentloaded")

        # 5) 目标区域截图
        region = page.locator(f"xpath={X_SHOT}").first
        await region.wait_for(state="visible", timeout=TIMEOUT_MS)
        await region.scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        await region.screenshot(path=str(out_path))

        return True, str(out_path.resolve())

    except Exception as e:
        return False, f"未找到图片: {e}"
    finally:
        await context.close()

# ---------- 单条 ----------
@app.post("/capture")
async def capture(
    request: Request,
    item: List[str] = Body(..., example=["编号001", "ACE2"])
):
    """
    Body: ["编号", "靶点名"]
    返回: {"code":..., "target":..., "path":...} 或 {"error":"未找到图片"}
    """
    if not isinstance(item, list) or len(item) < 2:
        return JSONResponse(status_code=400, content={"error": "请求体需为 [编号, 靶点名] 的列表"})
    code, target = str(item[0]), str(item[1])

    async with request.app.state.queue_lock:
        ok, ret = await _capture_one_raw(request, code, target)

    if ok:
        return {"code": code, "target": target, "path": ret}
    else:
        return {"code": code, "target": target, "error": "未找到图片"}

# ---------- 批量（map） ----------
@app.post("/capture_map")
async def capture_map(
    request: Request,
    items: Dict[str, str] = Body(..., example={"编号001": "ACE2", "编号002": "EGFR"})
):
    """
    Body: {"编号":"靶点名", ...}
    返回: {"output_dir": OUTPUT_DIR, "done": [成功保存的编号列表]}
    """
    if not isinstance(items, dict) or not items:
        return JSONResponse(status_code=400, content={"error": "请求体需为 {编号: 靶点名} 的对象"})

    done: List[str] = []

    # 整批任务串行排队；批内逐个处理
    async with request.app.state.queue_lock:
        for code, target in items.items():
            ok, _ = await _capture_one_raw(request, str(code), str(target))
            if ok:
                done.append(str(code))

    return {"output_dir": OUTPUT_DIR, "done": done}

if __name__ == "__main__":
    import uvicorn
    # 直接 python server.py 启动
    print("\n服务启动在 http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
