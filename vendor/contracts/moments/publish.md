# 朋友圈动作契约 v2 — Moments Action Contract v2（**发布 / 广播 publish**）

> 真相源: `agentic-contracts` 仓 · owner 见 CODEOWNERS（`moments/` → AMR/@gthneo）。**王总钦定: moments-publish 与所有契约一样由 AMR 统一持有**。
> fullwechat 本地副本 `~/as/fullwechet/docs/moments-action-contract-v2-publish.md` 是早期起草版（status「提案」），**已被本共享契约取代（superseded）**；以本文件为唯一真相源。

> **日期** 2026-06-28 ｜ **状态** 定稿（AMR 评审 fullwechat v2 草案后落定，待 fullwechat 实现）
> **from** AgenticMessageRouter (AMR) ｜ **to** fullwechat 后端 ｜ **co-consumer** AMP（Agentic Marketing Platform）
> **真相源** `agentic-contracts` 仓（本文件）—— AMR 定义契约，fullwechat 实现。fullwechat 早期草案见上方 superseded 说明。
> **关系** v1（同目录 `read-like.md`）= 朋友圈**读 + 点赞**，**仍然有效**；本 v2 **只新增「发布（publish / 一对多广播）」**，不重述读/点赞（DRY）。
> **姊妹契约** `../message/canonical.md` / `../send-target/send-target.md` / `../group-metadata/group-metadata.md`（同日）。

---

## §0 v2 是什么 / 不是什么

- **本契约是**：往**自己**朋友圈**发布一条动态**（一对多广播）的 AMR/AMP ↔ fullwechat 接口约束 —— 文案 + 图/视频 + 链接卡片 + 可见范围 + 幂等。
- **本契约不是**：读动态 / 点赞（见 v1）；不是评论（YAGNI，v1 §7 留位）；不是排期/批量/营销节奏（那是 AMR/AMP 上层的「事」，§8）。

**两个消费者**：
- **AMR**（关系运营）：关系向发布 —— 给某客户可见的内容，可与「维护某人」的事联动（发完顺手给该客户点赞）。
- **AMP**（Agentic Marketing Platform，营销广播）：**publish 的主用例** —— 营销图文/链接/活动一对多广播。**广播统一走这份契约，别另造。**

**真相源**：AMR 仓。后端有疑义 → 提 AMR，AMR 改本 spec → 后端按新版实现。后端不自行解释契约。

---

## §1 设计原则

1. **发布 = 最重的对外动作，HITL 不可破（王总钦定，§7）。** 发布是**一对多广播**，发出去无法收回。比点赞（§v1）更重：**永远人审后才发，永远不自动发，不进任何自动闸**（§8.3）。
2. **写动作幂等（广播尤其要）。** `idempotency_key` 由调用方给；同 key 在窗口内重复调 = no-op，返回原 `tid`。网络重试**绝不重复广播**（§6）。
3. **`media.ref` = 调用方提供的可取字节源（与 v1 读方向相反）。** v1 读：`ref` 是 fullwechat 托管、AMR GET 的解密端点。**v2 发：`ref` 是调用方（AMR/AMP）托管、fullwechat GET 的源**（HTTP URL 优先）。**同名字段、方向相反**，按本节口径区分（§4）。
4. **`text` 地板 + 非空内容约束。** `text` 必填键（可空串配纯图）；但 `text`/`media`/`link` **至少一个有内容**，不发空动态（§2.1）。
5. **能力分阶段声明 + 优雅降级。** `publish.{text,image,link,video}` 各自声明；调用方按声明降级（§9）。
6. **LLM 无关（铁律）。** 发布动作本身零 LLM：调用方给定信封 → fullwechat 机械执行。内容起草可用 LLM *assist*（AMR/AMP 侧），但**发布的 gate 是人、不是模型**；模型全不可用时人仍可手敲文案、照常发布。

---

## §2 发布信封（AMR/AMP → fullwechat）

```json
{
  "text": "新批次现货到仓，老客户私信享内购价。",
  "media": [
    { "kind": "image", "ref": "https://amp.example.com/assets/batch7/a.jpg", "mime": "image/jpeg" }
  ],
  "link": {
    "url": "https://example.com/promo",
    "title": "六月内购专场",
    "desc": "限本周",
    "thumb_ref": "https://amp.example.com/assets/batch7/thumb.jpg"
  },
  "visibility": { "mode": "exclude", "wxids": ["wxid_test_competitor"] },
  "idempotency_key": "amp-batch7-promo-20260628"
}
```

### §2.1 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | str | 是 | 文案正文。**纯图/纯视频可空串 `""`**；但与 `media`/`link` **不可同时为空**（见下「非空内容约束」）。 |
| `media` | arr | 否 | 图/视频列表（元素见 §2.2）。无则省略或 `[]`。`text`+`media` 共存 = 图文。 |
| `link` | obj | 否 | 分享链接卡片（见 §2.3）。**`media` 与 `link` 互斥**（朋友圈一条动态不能既是图文又是链接卡）；同给二者 → `ok:false, code:"INVALID_ENVELOPE"`。 |
| `visibility` | obj | 否 | 可见范围（见 §5）。**缺省 = 公开**。 |
| `idempotency_key` | str | **是** | 去重键（**v2 升格为必填**，广播不可重发，见 §6）。 |

**非空内容约束**：`text`（去空白后）、`media`、`link` 三者**至少一个有内容**。三者皆空 → `ok:false, code:"EMPTY_CONTENT"`，**fullwechat 拒发**（不发空白动态）。

### §2.2 `media` 元素

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kind` | `"image"` \| `"video"` | 是 | 媒体类型。一条动态内 `media` 必须同类（全图或单视频），混给 → `INVALID_ENVELOPE`。 |
| `ref` | str | 是 | **调用方提供的可取字节源**：fullwechat GET 下载后贴进朋友圈发表器。**HTTP(S) URL 优先**；本地路径仅当与 fullwechat 同主机时可用。取不到 → `MEDIA_FETCH_FAILED`。 |
| `mime` | str | 否 | 如 `"image/jpeg"`/`"video/mp4"`；给得出就给，帮 fullwechat 校验。 |

- **数量/大小/格式上限由 fullwechat 校验**（朋友圈图 ≤9 张、视频单条且受时长/大小限制）；超限 → `MEDIA_LIMIT_EXCEEDED`，不截断、不静默丢。

### §2.3 `link` 对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `url` | str | 是 | 分享链接 URL。 |
| `title` | str | 是 | 卡片标题。 |
| `desc` | str | 否 | 摘要。 |
| `thumb_ref` | str | 否 | 缩略图源（与 `media.ref` 同语义：调用方托管、fullwechat GET）。 |

---

## §3 端点与响应

### 3.1 端点
```
POST /api/moments/publish
Authorization: Bearer <token>
Content-Type: application/json
```
请求体 = §2 信封。**HTTPS**，bearer auth。

### 3.2 响应

**成功**：
```json
{ "ok": true, "tid": "snsTimeLine_self_1750000777" }
```
- `tid` = 新动态稳定 id（`sns.db SnsTimeLine`），供调用方**留痕 / 后续读取 / 给自己这条点赞**（与 v1 `tid` 同源同义）。
- 幂等命中（同 `idempotency_key` 重复）也返回 `ok:true` + **原 tid**（§6）。

**失败**：
```json
{ "ok": false, "code": "NOT_LOGGED_IN", "error": "human-readable detail" }
```

### 3.3 结构化错误码（与 send-target / canonical 对齐）

| `code` | 含义 | 调用方处理 |
|---|---|---|
| `NOT_LOGGED_IN` | 该账号未登录 | 提示人去登录；不重试 |
| `EMPTY_CONTENT` | text/media/link 三者皆空 | 调用方修内容（信封错，不重试原样） |
| `INVALID_ENVELOPE` | media 与 link 同给 / media 混类等结构错 | 调用方修信封 |
| `MEDIA_FETCH_FAILED` | `ref` 取不到字节 | 检查 ref 可达性；修后带**新** idempotency_key 重发 |
| `MEDIA_LIMIT_EXCEEDED` | 图>9 / 视频超限 | 调用方裁剪 |
| `VISIBILITY_INVALID` | visibility 模式/wxids 不合法（§5） | 调用方修 visibility |
| `PUBLISH_FAILED` | 发表器执行失败（selector/UI 异常） | 保留草稿、回报人；可同 key 重试（幂等保护） |

- **口径**：调用方按 `code` 决定动作，**不靠 HTTP 状态码区分语义**（HTTP 200 也可能 `ok:false`，对齐 send-target §2.1）。`code` 封闭枚举，新增 code 由 AMR 改 spec。

---

## §4 媒体（图 / 视频）—— `ref` 方向口径（重点）

> v1 与 v2 同名 `ref`、**方向相反**，是最易混点，单列口径：

| | v1（读） | v2（发） |
|---|---|---|
| `ref` 指向 | **fullwechat 托管**的解密取件端点 | **调用方（AMR/AMP）托管**的字节源 |
| 谁 GET | **AMR** GET（带 bearer） | **fullwechat** GET（下载后贴进发表器） |
| 优先形态 | `/api/media/...` 绝对 URL | HTTP(S) URL（本地路径仅同主机） |

- fullwechat 下载 `ref` → paste-image/paste-file + GTK 文件选择贴进朋友圈发表器。
- **安全**：fullwechat 只取**调用方信封里给的 `ref`**，不解析、不跟随内容里出现的其它链接；调用方负责 `ref` 来源可信。

---

## §5 可见范围 `visibility`（朋友圈特有）

```json
{ "mode": "public|private|include|exclude", "wxids": ["..."] }
```

| `mode` | 含义 | `wxids` |
|---|---|---|
| `public` | 公开（所有人可见） | 忽略 |
| `private` | 私密（仅自己可见） | 忽略 |
| `include` | 部分可见（仅这些人能看） | **必填非空** |
| `exclude` | 不给谁看（这些人看不到） | **必填非空** |

- **缺省**（`visibility` 省略）= `public`。
- `include`/`exclude` 给空 `wxids` 或非法 mode → `VISIBILITY_INVALID`。
- **AMR 用 `include` 做关系向定向发布**（只给某客户看）；**AMP 用 `public` 做营销广播**。

---

## §6 幂等（广播安全的命门）

- `idempotency_key` **必填**（§2.1）。fullwechat 维护已发 key→tid 映射（建议窗口 ≥24h）。
- **同 key 重复调** = no-op，返回 `ok:true` + **原 tid**，**绝不二次广播**。
- 网络超时/重试场景：调用方用**同一 key** 安全重试；只有**改了内容**才换新 key。
- 这是「系统永不替你发」在广播面的技术兜底：人审一次 → 一个 key → 最多广播一次。

---

## §7 HITL 铁律（王总钦定，最高准则，不可破）

发布 = **对外一对多广播**，是 AMR 体系里最重的对外写动作。

```
AMR/AMP 起草内容（含 LLM assist 可选）
   → 人（营销专员 / 王总）在 AMR/AMP 侧 review 确认（看全文 + 可见范围 + 媒体）
   → 才调 POST /api/moments/publish
```

- **fullwechat 收到调用 = 视为「已人审」，机械执行发布**；fullwechat **不自主发、不替人决定发什么、不补内容**。
- **系统永不跳过人的确认直接广播**（不管内容看起来多安全、多营销标准）。
- **留痕**：谁 / 何时 / 发了什么（信封 + 返回 `tid`），AMR/AMP 侧落审计。

---

## §8 AMR / AMP 侧消费语义（fullwechat 不实现）

> 本节是上层模型，记此帮后端理解接口设计；fullwechat 无需实现。

### 8.1 发布 = 「事」的一次对外动作
- AMR：一条关系向发布 = 某「维护某人 / 业务事」下的一次 interaction（对齐设计 spec §2 事模型）；发完可联动给该 person 点赞（v1）。
- AMP：一条营销广播 = 某营销活动「事」下的一次投放；内容由更上游 Agent 逆造、AMP 排期。

### 8.2 排期归上层
何时发、发几条、间隔（拟人 / 避风控）= AMR/AMP 决策；fullwechat 只在被调用时发**一条**（可后端兜底限速防风控）。对齐 v1 §5.4（排期归上层）。

### 8.3 发布在自动闸之外（与 phase-1 自动回复的边界）
- 自动沟通的**双闸**（话术库白名单 ∧ LLM 低风险）只覆盖**低风险会话回复**（寒暄/确认类安全话术）。
- **广播发布永不进任何自动闸、永不 arm 倒计时自动发** —— 它**总是**走 §7 的人审。观察/监管/自治三挡的「自治」也**不含**广播。
- 一句话：**会话里的安全寒暄可在监管下倒计时自动发；朋友圈广播永远要人亲手放行。**

---

## §9 能力声明 + 优雅降级

`/api/capabilities` 的 `moments` 扩展（在 v1 `read`/`like` 基础上加 `publish`）：
```json
{ "moments": { "read": true, "like": true,
  "publish": { "text": true, "image": true, "link": true, "video": false } } }
```

| 能力 | 含义 | 调用方降级 |
|---|---|---|
| `publish.text` | 支持纯文字发布 | `false` → AMR/AMP 隐藏发布入口 |
| `publish.image` | 支持带图发布 | `false` → 带图草稿灰掉，仅纯文/链接可发 |
| `publish.link` | 支持链接卡片 | `false` → 链接降级为文中明文 URL |
| `publish.video` | 支持视频 | `false`（v2 后期） → 视频草稿灰掉 |

- 分阶段上线：**text 先上，image/link 次之，video 后期**；调用方按声明降级，**绝不**对未声明能力硬发。
- `publish` 整项缺失 → 按「不支持发布」处理（只读 + 点赞，回落 v1）。

---

## §10 口径与边界

| 事项 | 口径 |
|---|---|
| `tid` 来源/稳定性 | 同 v1：`sns.db SnsTimeLine` 稳定主键；发布后读「self 最新一条」回 `tid`。 |
| `media` vs `link` 互斥 | 朋友圈一条动态不能既图文又链接卡；同给 → `INVALID_ENVELOPE`。 |
| 空动态 | text/media/link 皆空 → `EMPTY_CONTENT` 拒发。 |
| 媒体上限 | fullwechat 校验（图 ≤9 / 视频单条受限）；超限 → `MEDIA_LIMIT_EXCEEDED`，不静默截断。 |
| `idempotency_key` 窗口 | 建议 ≥24h；过窗后同 key 视为新发（调用方不应跨天复用 key）。 |
| 取消/删除已发动态 | v2 **不做**（YAGNI）。删动态是高危对外动作，留待专契约 + 强 HITL。扩展位：未来 `DELETE /api/moments/{tid}`，capabilities 另开 `publish.delete`。 |
| 评论 | v2 不纳入（同 v1 §7 YAGNI）。 |
| `direction` | 发布产物天然 `out`（自己发的）；读回时按 v1 §2.1 规则。 |
| 公开仓 | 本契约示例全合成（无真实 PII / token / wxid）。 |

---

## §11 fullwechat 实现侧参考（不占 AMR 契约决策）

| 项 | 实现参考 |
|---|---|
| 发布执行 | `plans/publish_moment.rs`：stage2 纯文字骨架已有；v2 定稿后补 image/link/video + **7 个发表器 selector 真机标定**（发表入口/输入框/可见范围/发表按钮）。 |
| 媒体贴入 | 下载 `ref` → paste-image/paste-file + GTK 文件选择贴进发表器。 |
| 可见范围 | 朋友圈「谁可以看」面板按 `visibility.mode` + `wxids` 勾选。 |
| 回 tid | 发布后读 `sns.db SnsTimeLine` self 最新一条。 |
| 幂等 | 本地存 key→tid 映射（窗口 ≥24h），命中直接回原 tid，不触发发表器。 |
| 限速 | 单次动作内可兜底发布间隔/避风控（拟人）；批量排期归上层。 |

---

## 协作约定

后端遇不清楚的口径：**提 AMR，AMR 改 spec，后端按新版实现**。不自行解释契约、不硬编码推断口径。AMR 是唯一真相源。

---

*本契约版本：v2 / 2026-06-28（评审 fullwechat 草案后定稿）。读+点赞见 v1。下次修订标 v2.1。*
