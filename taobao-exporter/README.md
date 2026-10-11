# Taobao Buyer Order Exporter

# 淘宝买家订单导出工具

A local Python + Playwright tool for exporting your own Taobao buyer order
history to CSV/XLSX. Manual login only. No CAPTCHA bypass. No credential
handling. Data stays on your computer.

一个本地运行的 Python + Playwright 淘宝买家订单导出工具，用于将你自己的淘宝买家订单
记录导出为 CSV/XLSX。仅支持手动登录，不绕过验证码，不处理账号密码，数据只保存在本地。

---

## Features / 功能

**English**

- Manual login in a real browser window — you stay in control.
- Persistent local browser profile, so you stay signed in across runs.
- Export to **CSV** or **XLSX**.
- **Manual page mode** (default): you advance pages yourself.
- **Safe auto-next mode** (opt-in): the tool turns the page for you, with
  human-like pacing.
- Deduplication and resume support — re-running never duplicates saved rows.
- Optional debug snapshots to help adapt to page-layout changes.
- No password / cookie / token handling.
- No CAPTCHA or slider handling — verification is always left to you.

**中文**

- 在真实浏览器窗口中手动登录，全程由你掌控。
- 使用本地持久化浏览器配置，多次运行之间保持登录状态。
- 支持导出为 **CSV** 或 **XLSX**。
- **手动翻页模式**（默认）：由你自己翻到下一页。
- **安全的自动翻页模式**（需手动开启）：工具会以接近真人的节奏帮你翻页。
- 去重与续传：重复运行不会重复写入已保存的记录。
- 可选的调试快照，便于在页面结构变化时进行适配。
- 不处理账号密码、Cookie 或 Token。
- 不处理验证码或滑块，验证始终由你本人完成。

---

## Safety and scope / 安全边界与适用范围

**English**

- Use this tool **only for your own Taobao account and your own order data**.
- The tool **does not** bypass CAPTCHA, slider verification, login challenges,
  anti-bot systems, or risk-control pages.
- The tool **does not** use private Taobao APIs.
- The tool **does not** ask for or store passwords, cookies, tokens, or
  localStorage / sessionStorage values.
- If any verification appears, **you must handle it manually** in the browser;
  the tool will stop and wait.
- All exported files are **saved locally**. Nothing is uploaded anywhere.

**中文**

- 本工具**仅可用于你自己的淘宝账号和你自己的订单数据**。
- 本工具**不会**绕过验证码、滑块验证、登录验证、反机器人系统或风控页面。
- 本工具**不会**调用淘宝的非公开接口。
- 本工具**不会**索取或保存账号密码、Cookie、Token，或 localStorage / sessionStorage 中的内容。
- 如果出现任何验证，**需由你本人在浏览器中手动完成**；此时工具会停下来等待。
- 所有导出的文件都**只保存在本地**，不会上传到任何地方。

---

## Installation / 安装

Requires **Python 3.11+**. Commands below use **Windows PowerShell** (replace
the folder with your own path).

需要 **Python 3.11 及以上**。以下命令以 **Windows PowerShell** 为例（请将文件夹替换为
你自己的路径）。

```powershell
# Go to your project folder / 进入你的项目文件夹
Set-Location "D:\taobao-order-exporter"

# Create and activate a virtual environment / 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies / 安装依赖
pip install -r requirements.txt

# Install the Chromium browser for Playwright / 安装 Playwright 所需的 Chromium 浏览器
playwright install chromium
```

> If activating the virtual environment is blocked, allow it for the current
> user once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
>
> 如果虚拟环境激活被阻止，可对当前用户执行一次：
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

---

## Usage / 使用方法

**Manual mode / 手动模式**

```powershell
python .\taobao_order_exporter.py --output .\taobao_orders.xlsx --format xlsx --debug
```

**Auto-next mode / 自动翻页模式**

```powershell
python .\taobao_order_exporter.py --output .\taobao_orders_all.xlsx --format xlsx --debug --auto-next --max-pages 100 --delay-min 10 --delay-max 25 --pause-every 10
```

**English — how it works**

1. The tool opens a browser window.
2. **Log in manually** if needed.
3. Navigate to your **buyer order list** page.
4. Switch to the terminal and press **Enter** only **after the order list is
   visible**. The current page is read and saved immediately.
5. In **manual mode**, you turn to the next page yourself, then press Enter
   again. Type **`q`** to quit.
6. In **auto-next mode**, the tool continues on its own until one of these
   happens: there is **no next page**, `--max-pages` is reached, the **page does
   not change** after turning, or a **verification page appears**. It pauses for
   confirmation every `--pause-every` pages, and you can press **Ctrl+C** anytime.

**中文 — 使用流程**

1. 工具会打开一个浏览器窗口。
2. 如有需要，请**手动登录**。
3. 进入你的**已买到的宝贝（买家订单列表）**页面。
4. 切换到终端，**确认订单列表已显示后**再按 **Enter**。当前页面会被读取并立即保存。
5. 在**手动模式**下，由你自己翻到下一页，然后再次按 Enter。输入 **`q`** 退出。
6. 在**自动翻页模式**下，工具会自动继续，直到出现以下任一情况：**没有下一页**、达到
   `--max-pages`、翻页后**页面没有变化**，或**出现验证页面**。每翻 `--pause-every` 页
   会暂停等待你确认，你也可以随时按 **Ctrl+C** 停止。

**Common options / 常用参数**

| Flag / 参数 | Default / 默认 | Meaning / 说明 |
|---|---|---|
| `--output` | `taobao_orders.csv` | Output file path / 输出文件路径 |
| `--format` | `csv` | `csv` or `xlsx` / 导出格式 |
| `--debug` | off | Save snapshots to `debug_snapshots/` / 保存调试快照 |
| `--auto-next` | off | Enable auto page turning / 启用自动翻页 |
| `--max-pages N` | 100 (auto) | Max pages this run / 本次最多翻页数 |
| `--delay-min S` | 6 | Min seconds before turning the page / 翻页前最短等待秒数 |
| `--delay-max S` | 15 | Max seconds before turning the page / 翻页前最长等待秒数 |
| `--pause-every N` | 20 | Pause every N pages (0 = never) / 每 N 页暂停一次（0 表示不暂停） |

---

## Output fields / 导出字段

| Field / 字段 | Description / 说明 |
|---|---|
| `order_date` | Order date / 下单日期 |
| `order_time` | Order time, if shown / 下单时间（如有显示） |
| `order_id` | Order number / 订单编号 |
| `shop_name` | Shop name / 店铺名称 |
| `item_title` | Item title / 商品标题 |
| `item_url` | Item link / 商品链接 |
| `item_sku` | Item options/SKU / 商品规格 |
| `quantity` | Quantity / 数量 |
| `unit_price` | Unit price / 单价 |
| `actual_paid` | Actual amount paid / 实付金额 |
| `order_status` | Order status / 订单状态 |
| `order_detail_url` | Order detail link / 订单详情链接 |
| `page_number_collected` | Page number collected from / 采集自第几页 |
| `collected_at` | Local timestamp of export / 本地导出时间 |

Rows are deduplicated by `order_id + item_title + actual_paid`. A redacted
example is in [`examples/sample_orders.csv`](examples/sample_orders.csv) (fake
data only).

记录会按 `order_id + item_title + actual_paid` 去重。脱敏示例见
[`examples/sample_orders.csv`](examples/sample_orders.csv)（仅为虚构数据）。

---

## Privacy notes / 隐私说明

**English**

- `browser_profile/` may contain your login state — **do not share it**.
- `debug_snapshots/` may contain order information — **do not share it**.
- Exported CSV/XLSX files contain personal order data — **do not commit them**.
- By default, receiver name / phone / address are **not** exported. The optional
  `--include-sensitive` flag only saves them if visibly present, and they are
  never printed to the terminal.
- All of the above are already listed in `.gitignore`, so git will not track them.

**中文**

- `browser_profile/` 可能包含你的登录状态，**请勿分享**。
- `debug_snapshots/` 可能包含订单信息，**请勿分享**。
- 导出的 CSV/XLSX 文件包含个人订单数据，**请勿提交到版本库**。
- 默认情况下**不会**导出收件人姓名 / 电话 / 地址。可选的 `--include-sensitive` 仅在页面
  上明确可见时才保存这些字段，并且绝不会打印到终端。
- 以上内容均已写入 `.gitignore`，git 不会追踪它们。

---

## Troubleshooting / 常见问题

**PowerShell activation blocked / PowerShell 激活被阻止**
Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate
the virtual environment again.
执行一次 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`，然后重新激活虚拟环境。

**Browser opens but not logged in / 浏览器打开但未登录**
Log in manually in the opened window. The persistent profile keeps you signed in
for next time.
请在打开的窗口中手动登录。持久化配置会让你下次保持登录状态。

**Stuck on `i.taobao.com/my_taobao` / 停留在“我的淘宝”页面**
Manually open the buyer order list page, then return to the terminal:
请手动打开买家订单列表页面，然后回到终端：
`https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm`

**No order cards matched / 未匹配到订单卡片**
Make sure the order list is fully visible and scrolled into view, then press
Enter again. If layout changed, run with `--debug` and adjust the selector lists
(`ORDER_CONTAINER_SELECTORS` / `ITEM_BLOCK_SELECTORS` / `FIELD_SELECTORS`) near
the top of `taobao_order_exporter.py`.
请确认订单列表已完整加载并滚动到可见区域，然后再次按 Enter。如果页面结构有变化，可加
`--debug` 运行，并调整 `taobao_order_exporter.py` 顶部的选择器列表。

**Auto-next cannot find the next page / 自动翻页找不到下一页**
This usually means the last page was reached, or the next-page control is
disabled/hidden. The tool stops on purpose instead of clicking blindly. Use
manual mode if needed.
通常表示已到最后一页，或“下一页”按钮被禁用/隐藏。工具会主动停止而不会盲目点击。必要
时可改用手动模式。

**Taobao shows verification / 淘宝出现验证**
Complete the verification yourself in the browser, return to your order list,
then continue. Slower pacing (`--delay-min` / `--delay-max`) and lower-frequency
use reduce how often this appears.
请在浏览器中自行完成验证，回到订单列表后再继续。放慢节奏（`--delay-min` /
`--delay-max`）并降低使用频率可减少其出现。

**Some `actual_paid` values are blank / 部分 `actual_paid` 为空**
The amount is only accepted from clear labels such as `实付款` / `实付` / `合计`
and must look like a normal amount. If a label is missing on the page, the field
is left blank rather than guessed.
该金额只会从 `实付款` / `实付` / `合计` 等明确标签中读取，且必须是正常金额。如果页面上
缺少对应标签，该字段会留空而不是猜测。

---

## Disclaimer / 免责声明

**English**

This is an unofficial local utility and is not affiliated with, endorsed by, or
sponsored by Taobao, Tmall, Alibaba, or any related entity. Use it only for your
own account and your own data. You are responsible for complying with applicable
terms, laws, and platform rules.

**中文**

本项目是非官方的本地工具，与淘宝、天猫、阿里巴巴或其关联方无关，也未获得其授权、认可
或赞助。请仅用于你自己的账号和你自己的数据。使用者需要自行遵守相关平台规则、法律法规
和服务条款。

---

## License / 许可证

Released under the [MIT License](LICENSE).

基于 [MIT License](LICENSE) 发布。
