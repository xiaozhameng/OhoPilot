//! OhoPilot 桌面壳（第 27 步）。
//!
//! 设计决策：壳只做「一个指向本机服务的原生窗口」，不在壳里嵌前端产物。
//! 原因：内核是 Python 常驻进程（SSE / 审批 / 上传都走 HTTP），
//! 壳里再嵌一份静态页面会变成两套来源、两套 CORS，还丢掉「同源」的省心。
//! 所以启动前先跑 `./start.sh`（或 `oho-server`），壳只是给网页穿上原生外衣。
//!
//! 将来要加托盘、全局快捷键、自动起内核子进程，都在这个 main 里长。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("OhoPilot 桌面壳启动失败");
}
