<div align="center">

# DeverAI

**鏈湴浼樺厛鐨?AI 宸ヤ綔鍙?路 鏁欒偛宸ュ叿 路 娓告垙涓庣爺绌跺疄楠屽満**

鎶?Agent 瑁呰繘鏈満杩涚▼锛屾妸鐪熷疄鏂囦欢涓庣湡瀹炲懡浠よ浜ゅ洖缁欎綘銆?
[鏃楄埌浜у搧 DeverAI](./DeverAI) 路 [椤圭洰鍦板浘](#-椤圭洰鍦板浘) 路 [蹇€熼€夊瀷](#-蹇€熼€夊瀷) 路 [鎶€鏈爤](#-鎶€鏈爤)

</div>

---

## 鎴戝湪鍋氫粈涔?
杩欓噷涓嶆槸銆屽張涓€涓亰澶╁３銆嶃€侱everAI 浣撶郴鍥寸粫涓夋潯涓荤嚎灞曞紑锛?
| 涓荤嚎 | 鏍稿績涓诲紶 | 浠ｈ〃浠撳簱 |
|------|----------|----------|
| **AI 宸ヤ綔鍙?* | UI 鍗?Agent 杩愯鍦帮紱鏈湴璁板繂鍗宠祫浜э紱绠楀姏鍙紓绉汇€佺姸鎬佸彲鍐峰 | `DeverAI` 路 `DeverAI-Hub` |
| **鏁欒偛涓庡涔?* | 鑷嫑/涓€?OI/涓撴敞鍔涳紝宸ュ叿浼樺厛銆佸彲鏈湴杩愯 | `learning-agent` 路 `zizhao-learning` 路 `OISystem` 路 `OpenIME` |
| **妯℃嫙涓庡疄楠?* | 鐢ㄥ彲鐜╃殑绯荤粺鐞嗚В澶嶆潅鐜板疄锛堢彮涓讳换銆佸崥澹€佹垬鏈琛岋級 | `homeroom-simulator` 路 `paper-writing-simulator` 路 `tactical-simulation` |

---

## 椤圭洰鍦板浘

> 鍏?25 涓粨搴擄紙22 鍏紑 / 3 绉佹湁锛夈€傛寜鐢ㄩ€旀祻瑙堬紝涓嶅繀鎸夊悕瀛楃寽銆?
### 0 路 鏃楄埌 路 AI 宸ヤ綔鍙?
| 浠撳簱 | 涓€鍙ヨ瘽 | 鐘舵€?|
|------|--------|------|
| **[DeverAI](https://github.com/DeverAI/DeverAI)** | 妗岄潰/缃戦〉/CLI/Lite 鍥涚 AI Agent 宸ヤ綔鍙般€傛枃浠跺垎鍖哄苟鍙戙€佷笓瀹跺洟 DAG銆佺畻鍔涙紓绉汇€乄orkTree 涓夊眰澶囦唤銆佸鍙戝鏍?| 涓讳骇鍝?|
| **[DeverAI-Hub](https://github.com/DeverAI/DeverAI-Hub)** | 涓汉涓婚〉銆佹彃浠朵笌鍖呭垎鍙戞灑绾斤紙鍚?model-router銆丆ordis 鎻掍欢锛?| 鏋㈢航 |
| **[dsh-harness-fork](https://github.com/DeverAI/dsh-harness-fork)** | DSH harness 鍒嗘敮锛屾帴鍏?DeverAI 璺敱璁捐 | 鍩虹璁炬柦 |

<details>
<summary>DeverAI 鑳藉姏閫熻</summary>

- **鍏叆鍙?*锛歅yQt 妗岄潰 / 瀹屾暣缃戦〉 / Lite 杩滄帶 / CLI / 鍚屾鏈嶅姟鍣?/ Cordis 鎻掍欢
- **鎬诲徃浠よ皟搴?*锛氫笓瀹跺洟 DAG 鍒嗗眰骞惰锛涙枃浠跺垎鍖鸿皟搴﹀櫒锛堝啓涓嶅悓鏂囦欢骞惰銆佸啓鍚屾枃浠朵覆琛岋級
- **绠楀姏婕傜Щ**锛氬伐浣滅姸鎬佸懆鏈熸帹閫佽嚜鏈夋湇鍔″櫒锛涘喎澶?Agent 缁亰锛涘紑鏈鸿嚜鍔ㄥ悎骞跺幓閲?- **WorkTree 澶囦唤瀹℃牳**锛氭枃浠剁骇蹇収 + 浠诲姟绾т細璇濆揩鐓?+ 渚濊禆鏍戞牎楠?+ 瀹¤鏃ュ織
- **瀹夊叏绾㈢嚎**锛氬伐浣滃尯 `resolve()` 闃茶秺鐣屻€佸嵄闄╁懡浠ゅ洓绔悓婧愩€佸鍙戦€愬瓧澶嶈堪 + 瀹℃牳鍗°€丼SRF 闃叉姢浠ｇ悊

</details>

### 1 路 鏁欒偛 路 瀛︿範绯荤粺

| 浠撳簱 | 涓€鍙ヨ瘽 | 褰㈡€?|
|------|--------|------|
| **[learning-agent](https://github.com/DeverAI/learning-agent)** | 棰樺簱 / OCR / AI 鎵规敼 / 涓撴敞妯″紡锛團astAPI + Web锛?| 鏈嶅姟 |
| **[learning-agent-v2](https://github.com/DeverAI/learning-agent-v2)** | v2锛欰ndroid 瀹㈡埛绔€佽鍫傜壒鎬с€佸紑鍙戞棩蹇?| 鏈嶅姟+绔?|
| **[zizhao-learning](https://github.com/DeverAI/zizhao-learning)** | 涓婃捣涓€冭嚜鎷涙瘡鏃ョ礌鏉愶細鍝插/鍘嗗彶/鍙よ瘲鏂?+ 鎵捐尙杩介棶 | 鏈嶅姟 |
| **[OISystem](https://github.com/DeverAI/OISystem)** | 淇℃伅瀛﹀ゥ璧涙闈笓娉ㄧ郴缁燂細AI 寮曞銆佸睆骞曞垎鏋愩€乑ZOI 闆嗘垚銆佸浘璁虹紪杈戝櫒 | 妗岄潰 |
| **[focus-tools](https://github.com/DeverAI/focus-tools)** | FocusTools / OISystem v1.0.0 鍙戝竷鍖?| 鍙戝竷 |
| **[OpenIME](https://github.com/DeverAI/OpenIME)** | 寰蒋鎷奸煶鐢ㄦ埛璇嶅簱绠″锛氬瀭鍩熸湳璇鍏ワ紙鏁欐潗/绔炶禌/榛戣瘽锛?| 妗岄潰 |
| **[zhongkao-widget](https://github.com/DeverAI/zhongkao-widget)** | 涓€冨€掕鏃舵闈㈠皬缁勪欢锛堝湪鏍＄姸鎬?妯¤€?鍐滃巻/璇剧▼锛?| 妗岄潰 |
| **[study-workbench](https://github.com/DeverAI/study-workbench)** | 涓汉瀛︿範宸ヤ綔鍙板悗绔笌閮ㄧ讲 | 鏈嶅姟 |

### 2 路 娓告垙 路 妯℃嫙鍣?
| 浠撳簱 | 涓€鍙ヨ瘽 | 鐜╂硶鍐呮牳 |
|------|--------|----------|
| **[homeroom-simulator](https://github.com/DeverAI/homeroom-simulator)** | 鐝富浠绘ā鎷熷櫒锛氫俊鎭糠闆?+ 瀹堕暱缇?+ 浼犻椈閾?+ LLM 瀵硅瘽 | 绠＄悊/鍙欎簨 |
| **[homeroom-simulator-flask](https://github.com/DeverAI/homeroom-simulator-flask)** | 鍚屼富棰?Flask 鍚庣 + 闈欐€佸墠绔増 | 绠＄悊/鍙欎簨 |
| **[paper-writing-simulator](https://github.com/DeverAI/paper-writing-simulator)** | 涔濇涓€鐢燂細鍗氬＋姣曚笟妯℃嫙鍣紙寮€棰樷啋鐩插鈫掔瓟杈╋紝鍏勾娓呴€€锛?| 鍛ㄥ洖鍚堢敓瀛?|
| **[battlian](https://github.com/DeverAI/battlian)** | 绛栫暐瀵规垬锛歅ython + Web 鍙岀増鏈紝AI 鎸囨尌瀹?| 绛栫暐瀵规垬 |
| **[tactical-simulation](https://github.com/DeverAI/tactical-simulation)** | FALCON-SIM 鎴樻湳椋炶锛氳捣椋?鎶曞脊/韬查伩/鎷︽埅绛変竷浠诲姟 | 椋炶妯℃嫙 |

### 3 路 妗岄潰宸ュ叿 路 鏁堢巼

| 浠撳簱 | 涓€鍙ヨ瘽 |
|------|--------|
| **[DeepTrans](https://github.com/DeverAI/DeepTrans)** | 鍒掕瘝缈昏瘧锛氫换鎰忓簲鐢ㄩ€変腑鏂囨湰 鈫?鎮诞绐楋紱灏忕背 MiMo / DeepSeek 鍙屽紩鎿?+ 鏈虹炕鍏滃簳 |
| **[AIrater](https://github.com/DeverAI/AIrater)** | 澶фā鍨嬩骇鍝佽瘎娴嬶細澶?API銆佽仈缃戞悳绱€佽嚜鍔ㄧ籂鍋忋€佸彲瑙嗗寲鍒嗘瀽 |

### 4 路 鐮旂┒ 路 瀹夊叏 路 鍒涗綔

| 浠撳簱 | 涓€鍙ヨ瘽 |
|------|--------|
| **[retrace](https://github.com/DeverAI/retrace)** | ReTrace锛歐indows 婕忔礊鏌ユ壘鍒嗘瀽鍙嶅悜宸ュ叿锛堟姄鍖?娉ㄥ唽琛?鍙嶇紪璇?MV3/LLM 瀹¤锛?|
| **[qinglian-platform](https://github.com/DeverAI/qinglian-platform)** | 闈掑皯骞翠簰鑱旂綉骞冲彴鍚堣鐩戞祴锛氫妇鎶?鈫?瀹℃牳 鈫?妗堜緥 / 璁哄潧 / 娉曞緥鐭ヨ瘑搴?|
| **[airender](https://github.com/DeverAI/airender)** | AI 寤烘ā椹卞姩 3D 瑙嗛娓叉煋娴佹按绾匡紙鍏樁娈碉紝鍙€夋湰鍦版墿鏁ｏ級 |
| **[dba-attention-genetic](https://github.com/DeverAI/dba-attention-genetic)** | 娉ㄦ剰鍔涢仐浼犵爺绌讹細Difference-Based Attention 鎷熷悎瀹為獙 |

### 5 路 鍐呴儴浠撳簱锛堢鏈夛級

| 浠撳簱 | 鐢ㄩ€?|
|------|------|
| `toolkit` | 缁樺浘 / 寤烘ā鑴氭湰涓庣鍙ｆ敞鍐岃〃 |
| `mindog` | MindDog K210 鍥涜冻鏁欒偛鏈哄櫒浜猴紙鍥轰欢 / 浜戠鑴?/ App锛?|
| `qoder-src-research` | Qoder SRC 鍙栬瘉鐮旂┒绗旇涓庤ˉ涓佽瘉鎹?|

---

## 蹇€熼€夊瀷

| 浣犳兂鈥?| 鍘昏繖閲?|
|-------|--------|
| 璺戜竴涓湰鍦?AI Agent锛屾搷浣滅湡瀹炴枃浠?| [`DeverAI`](https://github.com/DeverAI/DeverAI) |
| 涓婃捣涓€冭嚜鎷涙瘡鏃ョ礌鏉?+ 杩介棶 | [`zizhao-learning`](https://github.com/DeverAI/zizhao-learning) |
| OI 涓撴敞瀛︿範 / 瀵规帴 ZZOI | [`OISystem`](https://github.com/DeverAI/OISystem) |
| 鎶婅涔夋湳璇杩涘井杞嫾闊?| [`OpenIME`](https://github.com/DeverAI/OpenIME) |
| 鍒掕瘝缈昏瘧妗岄潰宸ュ叿 | [`DeepTrans`](https://github.com/DeverAI/DeepTrans) |
| 浣撻獙銆岀彮涓讳换 / 鍗氬＋銆嶇敓瀛樺帇鍔?| [`homeroom-simulator`](https://github.com/DeverAI/homeroom-simulator) 路 [`paper-writing-simulator`](https://github.com/DeverAI/paper-writing-simulator) |
| Windows 閫嗗悜涓庢紡娲炶瀵?| [`retrace`](https://github.com/DeverAI/retrace) |
| 璇勬祴澶氬澶фā鍨嬩骇鍝?| [`AIrater`](https://github.com/DeverAI/AIrater) |

---

## 鎶€鏈爤

- **涓昏瑷€**锛歅ython锛圥yQt6 / FastAPI / PySide6锛壜?JavaScript / TypeScript
- **褰㈡€?*锛氭闈?GUI 路 闆舵瀯寤洪潤鎬佸墠绔?路 CLI 路 鏈湴鏈嶅姟 路 Cordis 鎻掍欢 路 Chrome MV3
- **椋庢牸鍋忓ソ**锛歴tdlib-first銆佹湰鍦颁紭鍏堛€佸皯渚濊禆銆佸彲鎵撳寘銆佸彲绂荤嚎
- **LLM 鎺ュ叆**锛歄penAI 鍏煎锛圖eepSeek / Kimi / 鏅鸿氨 / 灏忕背 MiMo / Ollama 绛夛級

---

## 缁存姢绾﹀畾锛堣法浠撳叡鎬э級

1. **瀵嗛挜涓嶈繘 git**锛氱幆澧冨彉閲忔垨鏈湴 `api.txt` / `config.json`锛堝凡 gitignore锛夈€?2. **璁捐/鎶€鏈枃妗ｅ垎浠?*锛歚Design.md` / `Techniques.md` / `Fact.md` / `FreqErr.md` 鏄爣閰嶃€?3. **鍗遍櫓鎿嶄綔鍙璁?*锛氬懡浠ょ‘璁ゃ€佹枃浠跺揩鐓с€佸璁℃棩蹇楀敖閲忎笁浠跺榻愩€?4. **UI 涓嶅爢 emoji**锛氱粺涓€ SVG 鍥炬爣鎴?`[OK]` / `[X]` / `[!]` 鏂囨湰鏍囪銆?
---

## License

鍚勪粨搴撶嫭绔嬫巿鏉冿紝浠ユ牴鐩綍 `LICENSE` 涓哄噯锛堝父瑙佷负 AGPL-3.0 / GPL-3.0锛夈€傜綉缁滄彁渚涙湇鍔′笖淇敼鏈」鐩椂锛岃閬靛畧瀵瑰簲 copyleft 鏉℃銆?
<div align="center">

<sub>DeverAI 路 Local-first AI workbench & experiment field 路 鏈€鍚庢暣鐞嗕簬 2026-09</sub>

</div>
