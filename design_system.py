"""Shared visual layer. CSS/JS are local trusted assets, not document/model content."""
import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).resolve().parent / "assets"


def stylesheet():
    return _stylesheet((ASSETS / "workbench.css").stat().st_mtime_ns,
                       (ASSETS / "fonts/FunnelDisplay-500.ttf").stat().st_mtime_ns)


@lru_cache(maxsize=2)
def _stylesheet(css_version, font_version):
    font = base64.b64encode((ASSETS / "fonts/FunnelDisplay-500.ttf").read_bytes()).decode("ascii")
    return ('@font-face{font-family:"Funnel Display";font-style:normal;font-weight:500;font-display:swap;'
            f'src:url(data:font/ttf;base64,{font}) format("truetype");}}\n' +
            (ASSETS / "workbench.css").read_text(encoding="utf-8"))


def apply_theme():
    st.html("<style>" + stylesheet() + "</style>")


def header_html(login=False, has_result=False):
    documents = "".join(
        f'<div class="tc-document"><span class="tc-doc-code">{code}</span><div><strong>{name}</strong>'
        f'<small>{detail}</small></div><span class="tc-doc-dot" aria-hidden="true"></span></div>'
        for code, name, detail in [("PO", "采购订单", "约定数量与单价"),
                                   ("CI", "商业发票", "本次开票数量与金额"),
                                   ("PL", "装箱单", "装箱数量与净重、毛重")])
    route = "" if login else ('<div class="tc-route" aria-label="审核流程">' + "".join(
        f'<div class="tc-step {"is-current" if i == (2 if has_result else 0) else ""}"><span class="tc-step-no">{i+1:02d}</span><span>{name}</span></div>'
        + ('<span class="tc-step-arrow" aria-hidden="true">→</span>' if i < 2 else "")
        for i, name in enumerate(["准备单证", "执行核验", "复核与导出"])) + '</div>')
    return f'''<div class="tc-shell {'tc-login-intro' if login else ''}" lang="zh-CN" data-tc-shell data-view="{'login' if login else 'workbench'}">
<div class="tc-brandbar"><div class="tc-wordmark"><span class="tc-mark" aria-hidden="true">TC</span><strong>tradecheck<span style="color:var(--p-accent)">.</span></strong></div><small>贸审通 · 单证审核工作台</small></div>
<section class="tc-intro" aria-label="贸审通简介" data-enter><div><p class="tc-eyebrow">贸审通 TradeCheck Agent</p><h1 class="tc-title">跨境贸易单证审核</h1>
<p class="tc-lead">把订单、发票和装箱单放在一起核对。<br>查看每一处差异的原文，确认后复审并导出报告。</p><div class="tc-filetypes" aria-label="支持文件格式"><span>PDF</span><span>XLSX</span><span>CSV</span></div></div>
<div class="tc-documents" aria-label="核对的三类单证">{documents}<div class="tc-document-track" aria-hidden="true"><span></span></div></div></section>{route}</div>'''


def render_header(login=False, has_result=False):
    st.html(header_html(login, has_result))


def motion_sources():
    paths = ["vendor/gsap.min.js", "vendor/ScrollTrigger.min.js", "workbench-motion.js"]
    return _motion_sources(tuple((ASSETS / path).stat().st_mtime_ns for path in paths))


@lru_cache(maxsize=2)
def _motion_sources(asset_versions):
    # No externally loaded scripts, user-provided HTML, eval(), or parent-frame access.
    # Streamlit may host an AMD loader. Shadow module-loader identifiers locally
    # so the unmodified UMD distributions export to window, not a chart module.
    # Never replace the host's define/module/exports globals.
    scripts = [f"if (!window.{name}) {{\n(function(module, exports, define){{\n" +
               (ASSETS / path).read_text(encoding="utf-8") +
               "\n}).call({window: window}, undefined, undefined, undefined);\n}"
               for name, path in [("gsap", "vendor/gsap.min.js"), ("ScrollTrigger", "vendor/ScrollTrigger.min.js")]]
    scripts.append((ASSETS / "workbench-motion.js").read_text(encoding="utf-8"))
    return scripts


@lru_cache(maxsize=2)
def _motion_component(asset_versions):
    from streamlit.components.v2 import component
    scripts = motion_sources()
    # Use Streamlit's supported JS-component API. st.html's sanitizer can remove
    # complete minified distributions even with unsafe_allow_javascript=True.
    module = "\n".join(scripts[:2]) + "\nexport default function() {\ntry {\n" + scripts[2] + '''
const status = document.getElementById('tc-motion-check');
if (status) status.textContent = '本地动画验收：GSAP ' + (window.gsap?.version || '未加载') +
  '；根节点绑定 ' + (window.__tradecheckMotion ? '正常' : '无') +
  '；减少动态效果 ' + (matchMedia('(prefers-reduced-motion:reduce)').matches ? '开启' : '关闭');
const mounted = window.__tradecheckMotion;
return () => mounted?.destroy();
} catch(error) {
  const status = document.getElementById('tc-motion-check');
  if (status) status.textContent = '本地动画初始化失败：' + error.name + ': ' + error.message;
  throw error;
}
}
'''
    return component("tradecheck_motion", html='<div style="height:1px" aria-hidden="true"></div>', js=module, isolate_styles=True)


def enable_motion():
    paths = ["vendor/gsap.min.js", "vendor/ScrollTrigger.min.js", "workbench-motion.js"]
    versions = tuple((ASSETS / path).stat().st_mtime_ns for path in paths)
    record = st.session_state.get("record")
    _motion_component(versions)(key="tc_motion_runtime", height="content", data={
        "revision": st.session_state.get("revision", 0),
        "signed_in": bool(st.session_state.get("_db_login") or st.session_state.get("_login")),
        "has_result": bool(record and record[0].result is not None and record[2] is None),
    })


def login_heading():
    render_header(login=True)
    st.html('<div class="tc-shell tc-auth-label"><h2>登录工作台</h2><p>使用已开通的账号，查看自己的单证与审核任务。</p></div>')
    enable_motion()


def render_footer():
    st.html('<footer class="tc-shell tc-footer"><span>TradeCheck Agent · 贸审通</span><span>仅核对单证内容一致性，不提供海关法律合规保证。</span></footer>')
