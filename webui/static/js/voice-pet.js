/* ============ voice-pet.js — 语音助手「小龙」2.0 客户端核心 ============
 * 基于 dsh-plugin-pet 架构（浮层 + 状态驱动心情 + 配置持久化）改造。
 *
 * Adapted from dsh-plugin-pet (https://github.com/c-ling/dsh-plugin-pet)
 * Copyright (c) 2026 chenchao, MIT License
 * Modified for DeverAI voice assistant "小龙" — MIT License
 *
 * 能力：
 *   - 浮层宠物（拖拽/抚摸/心情动画），作为小龙视觉化身
 *   - 对话面板（多轮历史 + 双模输入：语音 + 文本）
 *   - 任务进度面板（任务列表 + 进度条 + 优先级）
 *   - 状态机驱动（idle/listening/thinking/responding/talking）
 *   - 随时对话 / 查询进度 / 插入消息 / 插入任务
 *
 * 模块开关：App.config.ENABLE_VOICE_ASSISTANT（默认 false，关闭即隐藏入口）
 */

(function () {
  "use strict";

  // ── 常量 ──
  var NS = "voice-pet";
  var POS_KEY = "voice-pet:pos";
  var MOOD_KEYS = ["idle", "thinking", "working", "happy", "sad", "waiting", "pet", "listening", "talking"];
  var SPRITE_SVG = {
    custom: 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNzAgMjcwIj4KICAgIDxkZWZzPgogICAgICAgIDxzdHlsZT4KICAgICAgICAgICAgQGtleWZyYW1lcyBicmVhdGhlIHsKICAgICAgICAgICAgICAgIDAlLCAxMDAlIHsgdHJhbnNmb3JtOiBzY2FsZSgxLCAxKTsgfQogICAgICAgICAgICAgICAgNTAlIHsgdHJhbnNmb3JtOiBzY2FsZSgxLjAzLCAwLjk3KTsgfQogICAgICAgICAgICB9CiAgICAgICAgICAgIEBrZXlmcmFtZXMgYm91bmNlIHsKICAgICAgICAgICAgICAgIDAlLCAxMDAlIHsgdHJhbnNmb3JtOiB0cmFuc2xhdGVZKDApOyB9CiAgICAgICAgICAgICAgICA1MCUgeyB0cmFuc2Zvcm06IHRyYW5zbGF0ZVkoLTZweCk7IH0KICAgICAgICAgICAgfQogICAgICAgICAgICBAa2V5ZnJhbWVzIHNxdWFzaCB7CiAgICAgICAgICAgICAgICAwJSwgMTAwJSB7IHRyYW5zZm9ybTogc2NhbGUoMSwgMSk7IH0KICAgICAgICAgICAgICAgIDMwJSB7IHRyYW5zZm9ybTogc2NhbGUoMS4wOCwgMC45Mik7IH0KICAgICAgICAgICAgICAgIDYwJSB7IHRyYW5zZm9ybTogc2NhbGUoMC45NiwgMS4wNCk7IH0KICAgICAgICAgICAgfQogICAgICAgICAgICBAa2V5ZnJhbWVzIGVhclR3aXRjaCB7CiAgICAgICAgICAgICAgICAwJSwgOTAlLCAxMDAlIHsgdHJhbnNmb3JtOiByb3RhdGUoMGRlZyk7IH0KICAgICAgICAgICAgICAgIDkzJSB7IHRyYW5zZm9ybTogcm90YXRlKC04ZGVnKTsgfQogICAgICAgICAgICAgICAgOTYlIHsgdHJhbnNmb3JtOiByb3RhdGUoNWRlZyk7IH0KICAgICAgICAgICAgfQogICAgICAgICAgICBAa2V5ZnJhbWVzIGVhclR3aXRjaFIgewogICAgICAgICAgICAgICAgMCUsIDg4JSwgMTAwJSB7IHRyYW5zZm9ybTogcm90YXRlKDBkZWcpOyB9CiAgICAgICAgICAgICAgICA5MSUgeyB0cmFuc2Zvcm06IHJvdGF0ZSg4ZGVnKTsgfQogICAgICAgICAgICAgICAgOTQlIHsgdHJhbnNmb3JtOiByb3RhdGUoLTVkZWcpOyB9CiAgICAgICAgICAgIH0KICAgICAgICAgICAgQGtleWZyYW1lcyBibGluayB7CiAgICAgICAgICAgICAgICAwJSwgOTIlLCAxMDAlIHsgdHJhbnNmb3JtOiBzY2FsZVkoMSk7IH0KICAgICAgICAgICAgICAgIDk0JSB7IHRyYW5zZm9ybTogc2NhbGVZKDAuMSk7IH0KICAgICAgICAgICAgICAgIDk2JSB7IHRyYW5zZm9ybTogc2NhbGVZKDEpOyB9CiAgICAgICAgICAgICAgICA5OCUgeyB0cmFuc2Zvcm06IHNjYWxlWSgwLjEpOyB9CiAgICAgICAgICAgIH0KICAgICAgICAgICAgQGtleWZyYW1lcyBzcXVpbnQgewogICAgICAgICAgICAgICAgMCUsIDg1JSwgMTAwJSB7IHRyYW5zZm9ybTogc2NhbGVZKDEpOyB9CiAgICAgICAgICAgICAgICA4OCUgeyB0cmFuc2Zvcm06IHNjYWxlWSgwLjQpOyB9CiAgICAgICAgICAgICAgICA5MSUgeyB0cmFuc2Zvcm06IHNjYWxlWSgwLjcpOyB9CiAgICAgICAgICAgIH0KICAgICAgICAgICAgQGtleWZyYW1lcyBsb29rQXJvdW5kIHsKICAgICAgICAgICAgICAgIDAlLCAxMDAlIHsgdHJhbnNmb3JtOiB0cmFuc2xhdGUoMCwgMCk7IH0KICAgICAgICAgICAgICAgIDI1JSB7IHRyYW5zZm9ybTogdHJhbnNsYXRlKDNweCwgLTJweCk7IH0KICAgICAgICAgICAgICAgIDUwJSB7IHRyYW5zZm9ybTogdHJhbnNsYXRlKC0ycHgsIDFweCk7IH0KICAgICAgICAgICAgICAgIDc1JSB7IHRyYW5zZm9ybTogdHJhbnNsYXRlKDFweCwgMnB4KTsgfQogICAgICAgICAgICB9CiAgICAgICAgICAgIEBrZXlmcmFtZXMgaGFwcHlCb3VuY2UgewogICAgICAgICAgICAgICAgMCUsIDEwMCUgeyB0cmFuc2Zvcm06IHRyYW5zbGF0ZVkoMCkgc2NhbGUoMSwgMSk7IH0KICAgICAgICAgICAgICAgIDI1JSB7IHRyYW5zZm9ybTogdHJhbnNsYXRlWSgtMTBweCkgc2NhbGUoMS4wNSwgMC45NSk7IH0KICAgICAgICAgICAgICAgIDUwJSB7IHRyYW5zZm9ybTogdHJhbnNsYXRlWSgwKSBzY2FsZSgwLjk1LCAxLjA1KTsgfQogICAgICAgICAgICAgICAgNzUlIHsgdHJhbnNmb3JtOiB0cmFuc2xhdGVZKC01cHgpIHNjYWxlKDEuMDIsIDAuOTgpOyB9CiAgICAgICAgICAgIH0KICAgICAgICAgICAgQGtleWZyYW1lcyB0aGlua2luZ1N3YXkgewogICAgICAgICAgICAgICAgMCUsIDEwMCUgeyB0cmFuc2Zvcm06IHJvdGF0ZSgwZGVnKTsgfQogICAgICAgICAgICAgICAgMjUlIHsgdHJhbnNmb3JtOiByb3RhdGUoMmRlZyk7IH0KICAgICAgICAgICAgICAgIDc1JSB7IHRyYW5zZm9ybTogcm90YXRlKC0yZGVnKTsgfQogICAgICAgICAgICB9CiAgICAgICAgICAgIEBrZXlmcmFtZXMgdGhpbmtpbmdUaWx0IHsKICAgICAgICAgICAgICAgIDAlLCAxMDAlIHsgdHJhbnNmb3JtOiByb3RhdGUoMGRlZykgdHJhbnNsYXRlWCgwKTsgfQogICAgICAgICAgICAgICAgMzAlIHsgdHJhbnNmb3JtOiByb3RhdGUoM2RlZykgdHJhbnNsYXRlWCgycHgpOyB9CiAgICAgICAgICAgICAgICA3MCUgeyB0cmFuc2Zvcm06IHJvdGF0ZSgtMWRlZykgdHJhbnNsYXRlWCgtMXB4KTsgfQogICAgICAgICAgICB9CiAgICAgICAgPC9zdHlsZT4KICAgIDwvZGVmcz4KICAgIDxnPgogICAgICAgIDwhLS0g6Lqr5L2T57uE77ya5YyF5ZCr5ZG85ZC45ZKM5by56LezIC0tPgogICAgICAgIDxnIHN0eWxlPSJ0cmFuc2Zvcm0tb3JpZ2luOiAxMzBweCAxNDBweDsgYW5pbWF0aW9uOiBicmVhdGhlIDNzIGVhc2UtaW4tb3V0IGluZmluaXRlOyI+CiAgICAgICAgICAgIDxnIHN0eWxlPSJ0cmFuc2Zvcm0tb3JpZ2luOiAxMzBweCAxNDBweDsgYW5pbWF0aW9uOiBib3VuY2UgMi41cyBlYXNlLWluLW91dCBpbmZpbml0ZTsiPgogICAgICAgICAgICAgICAgPCEtLSDouqvkvZMgLS0+CiAgICAgICAgICAgICAgICA8ZyB0cmFuc2Zvcm09InRyYW5zbGF0ZSgwIDIwKSB0cmFuc2xhdGUoMTMwIDEyMCkiPgogICAgICAgICAgICAgICAgICAgIDxjaXJjbGUgcj0iOTAiIGZpbGw9IiNlZGYwZjIiIHN0eWxlPSJ0cmFuc2Zvcm0tb3JpZ2luOiAwIDA7IGFuaW1hdGlvbjogc3F1YXNoIDRzIGVhc2UtaW4tb3V0IGluZmluaXRlOyI+CiAgICAgICAgICAgICAgICAgICAgPC9jaXJjbGU+CiAgICAgICAgICAgICAgICA8L2c+CgogICAgICAgICAgICAgICAgPCEtLSDlt6bogLPmnLUgLS0+CiAgICAgICAgICAgICAgICA8ZyB0cmFuc2Zvcm09InRyYW5zbGF0ZSg3NSA0OCkiIHN0eWxlPSJ0cmFuc2Zvcm0tb3JpZ2luOiAwIDA7IGFuaW1hdGlvbjogZWFyVHdpdGNoIDVzIGVhc2UtaW4tb3V0IGluZmluaXRlOyI+CiAgICAgICAgICAgICAgICAgICAgPGVsbGlwc2UgY3g9IjAiIGN5PSIwIiByeD0iMjgiIHJ5PSIyMCIgZmlsbD0iIzI4NUQ4RiIgdHJhbnNmb3JtPSJyb3RhdGUoLTIwKSIvPgogICAgICAgICAgICAgICAgICAgIDxlbGxpcHNlIGN4PSIzIiBjeT0iMiIgcng9IjE4IiByeT0iMTIiIGZpbGw9IiM1NDlGQzQiIHRyYW5zZm9ybT0icm90YXRlKC0yMCkiLz4KICAgICAgICAgICAgICAgIDwvZz4KCiAgICAgICAgICAgICAgICA8IS0tIOWPs+iAs+actSAtLT4KICAgICAgICAgICAgICAgIDxnIHRyYW5zZm9ybT0idHJhbnNsYXRlKDE4NSA0OCkiIHN0eWxlPSJ0cmFuc2Zvcm0tb3JpZ2luOiAwIDA7IGFuaW1hdGlvbjogZWFyVHdpdGNoUiA0LjVzIGVhc2UtaW4tb3V0IGluZmluaXRlOyI+CiAgICAgICAgICAgICAgICAgICAgPGVsbGlwc2UgY3g9IjAiIGN5PSIwIiByeD0iMjgiIHJ5PSIyMCIgZmlsbD0iIzI4NUQ4RiIgdHJhbnNmb3JtPSJyb3RhdGUoMjApIi8+CiAgICAgICAgICAgICAgICAgICAgPGVsbGlwc2UgY3g9Ii0zIiBjeT0iMiIgcng9IjE4IiByeT0iMTIiIGZpbGw9IiM1NDlGQzQiIHRyYW5zZm9ybT0icm90YXRlKDIwKSIvPgogICAgICAgICAgICAgICAgPC9nPgoKICAgICAgICAgICAgICAgIDwhLS0g5aS06YOo6JOd6Imy6Iqx57q5IC0tPgogICAgICAgICAgICAgICAgPHBhdGggZmlsbD0iIzI4NUQ4RiIKICAgICAgICAgICAgICAgICAgICBkPSJNMjExLjYgNTFjNi44LTcuOCAxNS0xMSAyNC4yLTcuNSA4LjMgMyAxMyA5LjggMTIgMTkgMTcgMTIuOCAxOC4zIDE5IDcuNiAzOC41IDYgNiAxMC43IDEyLjggOS44IDIyLjMtLjIgMi4zIDEuNyA0LjggMi42IDcuMiA1LjIgMTUuNC00LjYgMjguMi0yMC42IDI3bC0xNi4zLTEuN2M1IDUuNCA5IDkuMiAxMi41IDEzLjMgOC40IDkuNiAxMCAxNy40IDUuNCAyNS41LTQuNyA4LjItMTIuNSAxMS4yLTI0LjIgOS40bC02LTEuNWMwIDIuOC4yIDUuMi41IDcuNiAxLjIgOS4yIDAgMTcuNC04LjYgMjIuNi03LjcgNC43LTE5LjUgMy4zLTI3LTMuMi0zLjYtMy02LjYtNy0xMC0xMC42LTUuNSAyLjQtNSA3LjctNiAxMi4yLTMgMTMtNy4yIDE3LjYtMTcuMiAxOS05LjUgMS0xNS0yLjgtMjAuOC0xNC43LS4zLS42LTEtMS0yLTItNi41IDcuNi0xMy4yIDE1LTI0LjIgMTUtOS40IDAtMTUuMi02LjMtMTguNC0yMGwtOS43LTUuNGMtMTMuNCA4LjUtMjIuMiAxMC0zNC02LjItOS0xLTE4LTIuMi0yMS41LTEyLjYtNC0xMS4zIDEuNi0xOS4zIDEyLjQtMjYuOGwtMTIuNSAxQzYgMTc4LjUtMy4zIDE2NiAxIDE1M2MyLjItNi43IDUuMy0xMyA3LjUtMTkuNyA0LTEyIDUtMjQuMi0uOC0zNi4zLTEuNy0zLjUtMy40LTguNy0yLTExLjYgMy43LTcuOCA5LjItMTQuNyAxMy42LTIyIDUtOC42IDEyLjYtMTEgMjAuMi05LjYgNy4yLTEwIDEzLTE5LjMgMjAuMy0yNy40IDMuNC0zLjcgOS41LTUgMTYuNC04LjJDODguNyAzIDk4LjggMy43IDExNCAyMy40YzguNS05LjggMTAuMi0xMC40IDI2LjgtOS4zQzE0NS40IDMuNiAxNTQtMS43IDE2NS41LjhjOS42IDIgMTQgMTAuMiAxNS4yIDIwLjcgOS43LTQuNiAxOS40LTcuNiAyNy41IDEuNSA3LjMgOCA1LjMgMTcuNyAzLjQgMjh6TTE4NiA5MC44Yy0xNy41LS4yLTMxIDEyLjctMzAuOCAyOS42IDAgMTcuMyAxMy4zIDMxIDMwIDMxIDE2LjYgMCAyOS42LTEzLjUgMzAtMzAuOC40LTE0LTEzLTMxLjctMjkuMy0yOS44ek04NCA4OC42Yy0xNy43LS4yLTMwLjcgMTItMzEgMjkuMyAwIDE3LjIgMTMgMzEgMjkuNiAzMS4yIDE2LjcuMiAzMC4zLTEzLjQgMzAuNC0zMC40LjItMTctMTIuNC0zMC0yOS0zMC4yeiIKICAgICAgICAgICAgICAgICAgICB0cmFuc2Zvcm09InRyYW5zbGF0ZSgwIDIwKSIgLz4KICAgICAgICAgICAgICAgIDxwYXRoIGZpbGw9IiM1NDlGQzQiCiAgICAgICAgICAgICAgICAgICAgZD0iTTI1OCA3MS40YzEuNiA2LjcgMS44IDkuNiAxLjggMTEuNyAwIDIuNS0uNyAzLjgtNCAxNyA3LjYtMTMuOCA4LjgtMjAuOCAyLTI4LjZ6TTIzMi41IDE1NmwxNC42IDEuNGMxNiAxLjMgMjYtMTEuNCAyMC42LTI3LS44LTIuMy0yLjgtNC44LTIuNi03IC43LTcuMy0xLjgtMTMtNS43LTE3LjggMiAzLjcgMyA3LjggMi40IDEyLjctLjMgMi4zLS40IDMuNiAxLjIgNi43IDkgMTcuNi01LjcgMjguMi0xMC42IDMwLTguNCAyLjgtMjAgMS0yMCAxem0xMyAzMy41Yy00LjYgOC4yLTEyLjQgMTEuMi0yNCA5LjQtMi0uNC0zLjctMS02LjItMS41LjIgMi44LjMgNS4yLjYgNy42IDEuMiA5LjIgMCAxNy40LTguNiAyMi42LTcuNyA0LjctMTkuNSAzLjMtMjctMy4yLTMuNi0zLTYuNi03LTEwLTEwLjYtNS41IDIuNC01IDcuNy02IDEyLjItMyAxMy03LjIgMTcuNi0xNy4yIDE5LTYuOC44LTExLjYtMS0xNi02LjYgNS40IDkuNSAxMC42IDEyLjYgMTkuMiAxMS41IDEwLTEuMyAxNC4yLTYgMTctMTkgMS00LjQuNy05LjggNi4yLTEyLjIgMy40IDMuNiA2LjQgNy41IDEwIDEwLjYgNy41IDYuNSAxOS4zIDcuOCAyNyAzLjIgOC41LTUuMiAxMC0xMy40IDguNi0yMi41LS4zLTIuMy0uNC00LjgtLjYtNy41bDYgMS40YzExLjggMS44IDE5LjYtMS4zIDI0LjItOS41IDQtNy4zIDMuMi0xNC4zLTMtMjIuNSAzLjIgNiAzIDExLjctLjIgMTcuNXoiCiAgICAgICAgICAgICAgICAgICAgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMCAyMCkiIC8+CgogICAgICAgICAgICAgICAgPCEtLSDohLjpg6jnu4TvvJrnnLznnZsgKyDnnLznnZvliqjnlLsgLS0+CiAgICAgICAgICAgICAgICA8ZyB0cmFuc2Zvcm09InRyYW5zbGF0ZSgwIDIwKSI+CiAgICAgICAgICAgICAgICAgICAgPCEtLSDlt6bnnLznmb0gLS0+CiAgICAgICAgICAgICAgICAgICAgPGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMTAwIDExOCkiPgogICAgICAgICAgICAgICAgICAgICAgICA8ZWxsaXBzZSByeD0iMjIiIHJ5PSIyNCIgZmlsbD0iI2ZmZmZmZiIvPgogICAgICAgICAgICAgICAgICAgICAgICA8IS0tIOW3puecvOeQgyAtLT4KICAgICAgICAgICAgICAgICAgICAgICAgPGcgc3R5bGU9InRyYW5zZm9ybS1vcmlnaW46IDAgMDsgYW5pbWF0aW9uOiBibGluayA0cyBlYXNlLWluLW91dCBpbmZpbml0ZSwgbG9va0Fyb3VuZCA2cyBlYXNlLWluLW91dCBpbmZpbml0ZTsiPgogICAgICAgICAgICAgICAgICAgICAgICAgICAgPGNpcmNsZSByPSIxNCIgZmlsbD0iIzExMSIvPgogICAgICAgICAgICAgICAgICAgICAgICAgICAgPGNpcmNsZSBjeD0iLTQiIGN5PSItNSIgcj0iNSIgZmlsbD0iI2ZmZiIgb3BhY2l0eT0iMC44NSIvPgogICAgICAgICAgICAgICAgICAgICAgICA8L2c+CiAgICAgICAgICAgICAgICAgICAgICAgIDwhLS0g5bem55y855yv55y86KaG55uWIC0tPgogICAgICAgICAgICAgICAgICAgICAgICA8cmVjdCB4PSItMjQiIHk9Ii0yMCIgd2lkdGg9IjQ4IiBoZWlnaHQ9IjIwIiBmaWxsPSIjZWRmMGYyIiBzdHlsZT0idHJhbnNmb3JtLW9yaWdpbjogMCAwOyBhbmltYXRpb246IHNxdWludCA1LjVzIGVhc2UtaW4tb3V0IGluZmluaXRlOyIvPgogICAgICAgICAgICAgICAgICAgIDwvZz4KICAgICAgICAgICAgICAgICAgICA8IS0tIOWPs+ecvOeZvSAtLT4KICAgICAgICAgICAgICAgICAgICA8ZyB0cmFuc2Zvcm09InRyYW5zbGF0ZSgxNjAgMTE4KSI+CiAgICAgICAgICAgICAgICAgICAgICAgIDxlbGxpcHNlIHJ4PSIyMiIgcnk9IjI0IiBmaWxsPSIjZmZmZmZmIi8+CiAgICAgICAgICAgICAgICAgICAgICAgIDwhLS0g5Y+z55y855CDIC0tPgogICAgICAgICAgICAgICAgICAgICAgICA8ZyBzdHlsZT0idHJhbnNmb3JtLW9yaWdpbjogMCAwOyBhbmltYXRpb246IGJsaW5rIDRzIGVhc2UtaW4tb3V0IGluZmluaXRlIDAuMXMsIGxvb2tBcm91bmQgNnMgZWFzZS1pbi1vdXQgaW5maW5pdGU7Ij4KICAgICAgICAgICAgICAgICAgICAgICAgICAgIDxjaXJjbGUgcj0iMTQiIGZpbGw9IiMxMTEiLz4KICAgICAgICAgICAgICAgICAgICAgICAgICAgIDxjaXJjbGUgY3g9Ii00IiBjeT0iLTUiIHI9IjUiIGZpbGw9IiNmZmYiIG9wYWNpdHk9IjAuODUiLz4KICAgICAgICAgICAgICAgICAgICAgICAgPC9nPgogICAgICAgICAgICAgICAgICAgICAgICA8IS0tIOWPs+ecvOecr+ecvOimhuebliAtLT4KICAgICAgICAgICAgICAgICAgICAgICAgPHJlY3QgeD0iLTI0IiB5PSItMjAiIHdpZHRoPSI0OCIgaGVpZ2h0PSIyMCIgZmlsbD0iI2VkZjBmMiIgc3R5bGU9InRyYW5zZm9ybS1vcmlnaW46IDAgMDsgYW5pbWF0aW9uOiBzcXVpbnQgNS41cyBlYXNlLWluLW91dCBpbmZpbml0ZSAwLjFzOyIvPgogICAgICAgICAgICAgICAgICAgIDwvZz4KICAgICAgICAgICAgICAgIDwvZz4KCiAgICAgICAgICAgICAgICA8IS0tIOiFrue6oiAtLT4KICAgICAgICAgICAgICAgIDxlbGxpcHNlIGN4PSI3MiIgY3k9IjE1NSIgcng9IjE2IiByeT0iMTAiIGZpbGw9IiNmZmIzYzYiIG9wYWNpdHk9IjAuNDUiIHN0eWxlPSJ0cmFuc2Zvcm0tb3JpZ2luOiAwIDA7IGFuaW1hdGlvbjogYnJlYXRoZSAzcyBlYXNlLWluLW91dCBpbmZpbml0ZTsiLz4KICAgICAgICAgICAgICAgIDxlbGxpcHNlIGN4PSIxODgiIGN5PSIxNTUiIHJ4PSIxNiIgcnk9IjEwIiBmaWxsPSIjZmZiM2M2IiBvcGFjaXR5PSIwLjQ1IiBzdHlsZT0idHJhbnNmb3JtLW9yaWdpbjogMCAwOyBhbmltYXRpb246IGJyZWF0aGUgM3MgZWFzZS1pbi1vdXQgaW5maW5pdGUgMC4zczsiLz4KCiAgICAgICAgICAgICAgICA8IS0tIOm8u+WtkCAtLT4KICAgICAgICAgICAgICAgIDxlbGxpcHNlIGN4PSIxMzAiIGN5PSIxNDgiIHJ4PSI2IiByeT0iNC41IiBmaWxsPSIjMjg1RDhGIiBvcGFjaXR5PSIwLjciLz4KICAgICAgICAgICAgICAgIDwhLS0g5Zi05be0IC0tPgogICAgICAgICAgICAgICAgPHBhdGggZD0iTTEyMCAxNTggUTEzMCAxNjYgMTQwIDE1OCIgc3Ryb2tlPSIjMjg1RDhGIiBzdHJva2Utd2lkdGg9IjMiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgZmlsbD0ibm9uZSIvPgogICAgICAgICAgICA8L2c+CiAgICAgICAgPC9nPgogICAgPC9nPgogICAgPCEtLSDliqjnlLvlvqrnjq/lmaggLS0+CiAgICA8cmVjdCB3aWR0aD0iMSIgaGVpZ2h0PSIxIiBzdHlsZT0iZmlsbDpub25lO3N0cm9rZTpub25lIj4KICAgICAgICA8c2V0IGF0dHJpYnV0ZU5hbWU9IngiIGJlZ2luPSIwczsgbG9vcC5lbmQiIGR1cj0iMTJzIiB0bz0iMCIgaWQ9Imxvb3AiLz4KICAgIDwvcmVjdD4KPC9zdmc+',
    blob: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><path d="M32 6c14 0 24 9 24 22 0 11-7 20-17 24-8 3-15 1-23-2-9-4-14-10-14-18 0-14 12-26 30-26z" fill="#6d8dff"/><circle cx="24" cy="30" r="3.4" fill="#1b2350"/><circle cx="40" cy="30" r="3.4" fill="#1b2350"/><circle cx="25" cy="29" r="1.2" fill="#fff"/><circle cx="41" cy="29" r="1.2" fill="#fff"/><path d="M28 40q4 3 8 0" stroke="#1b2350" stroke-width="2.4" stroke-linecap="round" fill="none"/></svg>',
    cat: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><path d="M16 26l-8-6 4 12zM48 26l8-6-4 12z" fill="#ffb36b"/><circle cx="32" cy="32" r="20" fill="#ffb36b"/><path d="M12 24l-4-8M52 24l4-8" stroke="#8a5a2b" stroke-width="3" stroke-linecap="round" fill="none"/><circle cx="24" cy="30" r="3.4" fill="#40260f"/><circle cx="40" cy="30" r="3.4" fill="#40260f"/><path d="M29 38l-1.5 2 4 2 3-3.5z" fill="#40260f"/><path d="M24 40q4 4 8 0" stroke="#8a5a2b" stroke-width="2.4" stroke-linecap="round" fill="none"/></svg>',
    robot: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect x="14" y="18" width="36" height="32" rx="8" fill="#b8c4d9"/><rect x="14" y="18" width="36" height="32" rx="8" fill="none" stroke="#55617a" stroke-width="2.4"/><path d="M32 18v-7M22 11h20" stroke="#55617a" stroke-width="2.6" stroke-linecap="round" fill="none"/><circle cx="32" cy="11" r="3" fill="#6d8dff"/><rect x="21" y="26" width="9" height="8" rx="2" fill="#26324d"/><rect x="34" y="26" width="9" height="8" rx="2" fill="#26324d"/><circle cx="25.5" cy="28.5" r="1.4" fill="#7fe3ff"/><circle cx="38.5" cy="28.5" r="1.4" fill="#7fe3ff"/><path d="M29 40h6" stroke="#55617a" stroke-width="2.2" stroke-linecap="round"/></svg>',
    axolotl: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><path d="M18 18q-4-8 2-12 7-4 10 2l2 4 2-4q3-6 10-2 6 4 2 12-2 4-8 5-6 1-6-1 0 2-6 1-6-1-8-5z" fill="#ff9ec4"/><ellipse cx="32" cy="38" rx="17" ry="14" fill="#ffb3cf"/><circle cx="25" cy="36" r="3" fill="#4a1b2e"/><circle cx="39" cy="36" r="3" fill="#4a1b2e"/><path d="M29 43q3 2.5 6 0" stroke="#d84f83" stroke-width="2.4" stroke-linecap="round" fill="none"/><circle cx="18" cy="42" r="2.6" fill="#ff8fb4"/><circle cx="46" cy="42" r="2.6" fill="#ff8fb4"/></svg>',
    duck: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><circle cx="32" cy="34" r="19" fill="#ffd94d"/><circle cx="26" cy="26" r="14" fill="#ffd94d"/><path d="M10 22q14-8 28 0l4 5q-3 6-11 7-10 1-16-3-6-4-5-9z" fill="#ff9d2e"/><circle cx="26" cy="22" r="3" fill="#331c00"/><circle cx="38" cy="24" r="3" fill="#331c00"/><path d="M24 40q8 4 16 0" stroke="#e0a800" stroke-width="2.6" stroke-linecap="round" fill="none"/></svg>',
    ghost: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><path d="M16 52V28a16 16 0 0 1 32 0v24l-4-4-4 4-4-4-4 4-4-4-4 4-4-4z" fill="#dbe6ff"/><path d="M16 52V28a16 16 0 0 1 32 0v24" fill="none" stroke="#8fa6d9" stroke-width="2"/><ellipse cx="24" cy="28" rx="3.6" ry="4.6" fill="#2b3a6b"/><ellipse cx="40" cy="28" rx="3.6" ry="4.6" fill="#2b3a6b"/><ellipse cx="32" cy="38" rx="3.2" ry="4" fill="#2b3a6b"/></svg>',
  };

  var QUIPS = ["在的", "摸鱼中...", "有需要就喊我", "盯 ---", "要不要喝口水?", "继续加油"];
  var MOOD_BUBBLE = {
    idle: "",
    thinking: "在想...",
    working: "干活中...",
    happy: "搞定!",
    sad: "呜...出错了",
    waiting: "需要你确认",
    pet: "\u2661",
    listening: "在听...",
    talking: "在说...",
  };

  // ── DOM 引用 ──
  var root = null;       // 浮层根
  var petBody = null;    // 宠物本体
  var bubble = null;     // 气泡
  var panel = null;      // 对话面板
  var chatHistory = null;
  var textInput = null;
  var micBtn = null;
  var camBtn = null;
  var camVideo = null;
  var camStream = null;   // v8.36：摄像头 MediaStream——仅用户显式开启；关面板即断
  var statusEl = null;

  // ── 状态 ──
  var _state = "idle";
  var _pos = loadPos();
  var _scale = 1;
  var _name = "小龙";
  var _sprite = "custom";
  var _visible = true;
  var _convMode = "feedback";
  var _conversations = [];
  var _dragMoved = false;
  var _dragStart = null;

  // ── 初始化 ──
  function init() {
    if (!App.config.ENABLE_VOICE_ASSISTANT) return;
    injectStyles();
    createDOM();
    bindEvents();
    loadConfig();
    renderPet();
  }

  // ── 注入样式 ──
  function injectStyles() {
    if (document.getElementById(NS + "-styles")) return;
    var css = [
      "#" + NS + "-root{position:absolute;bottom:24px;right:24px;z-index:999;display:flex;flex-direction:column;align-items:flex-end;gap:8px;user-select:none;-webkit-user-select:none}",
      "#" + NS + "-root[data-hidden='true']{display:none}",
      "#" + NS + "-body{width:calc(72px*var(" + NS + "-scale,1));height:calc(72px*var(" + NS + "-scale,1));cursor:grab;filter:drop-shadow(0 3px 8px rgba(0,0,0,.18));transform-origin:50% 100%;transition:transform .15s ease}",
      "#" + NS + "-body:active{cursor:grabbing}",
      "#" + NS + "-body svg{width:100%;height:100%;display:block}",
      "#" + NS + "-root[data-mood='idle'] #" + NS + "-body{animation:" + NS + "-breathe 3.4s ease-in-out infinite}",
      "#" + NS + "-root[data-mood='thinking'] #" + NS + "-body{animation:" + NS + "-tilt 1.6s ease-in-out infinite}",
      "#" + NS + "-root[data-mood='working'] #" + NS + "-body{animation:" + NS + "-bounce .5s ease-in-out infinite}",
      "#" + NS + "-root[data-mood='happy'] #" + NS + "-body{animation:" + NS + "-hop .5s ease-in-out 4}",
      "#" + NS + "-root[data-mood='sad'] #" + NS + "-body{animation:" + NS + "-droop 2.6s ease-in-out infinite}",
      "#" + NS + "-root[data-mood='waiting'] #" + NS + "-body{animation:" + NS + "-nudge 1.4s ease-in-out infinite}",
      "#" + NS + "-root[data-mood='listening'] #" + NS + "-body{animation:" + NS + "-pulse 1s ease-in-out infinite}",
      "#" + NS + "-root[data-mood='talking'] #" + NS + "-body{animation:" + NS + "-bounce .6s ease-in-out infinite}",
      "@keyframes " + NS + "-breathe{0%,100%{transform:scale(1)}50%{transform:scale(1.04,1.07)}}",
      "@keyframes " + NS + "-tilt{0%,100%{transform:rotate(-3deg)}50%{transform:rotate(3deg)}}",
      "@keyframes " + NS + "-bounce{0%,100%{transform:translateY(0)}35%{transform:translateY(-10px) scale(1.04,.96)}70%{transform:translateY(0) scale(.97,1.03)}}",
      "@keyframes " + NS + "-hop{0%,100%{transform:translateY(0)}40%{transform:translateY(-14px) rotate(-4deg)}70%{transform:translateY(0)}}",
      "@keyframes " + NS + "-droop{0%,100%{transform:translateY(2px) scale(.97,1.04)}50%{transform:translateY(2px) scale(.97,1.04)}}",
      "@keyframes " + NS + "-nudge{0%,100%{transform:translateY(0) rotate(0)}50%{transform:translateY(-4px) rotate(-2deg)}}",
      "@keyframes " + NS + "-pulse{0%,100%{transform:scale(1);filter:drop-shadow(0 3px 8px rgba(0,0,0,.18))}50%{transform:scale(1.08);filter:drop-shadow(0 3px 14px rgba(109,141,255,.5))}}",
      "#" + NS + "-bubble{max-width:220px;padding:6px 10px;border-radius:10px;background:var(--dsw-alias-button-floating-fill,#fff);border:1px solid var(--dsw-alias-border-l1,rgba(0,0,0,.1));box-shadow:0 2px 8px rgba(0,0,0,.1);font-size:12px;line-height:1.4;color:inherit;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none}",
      "#" + NS + "-bubble:empty{display:none}",
      // 对话面板
      "#" + NS + "-panel{position:absolute;bottom:calc(80px*var(" + NS + "-scale,1) + 12px);right:0;width:320px;max-height:480px;background:var(--dsw-alias-bg-layer-2,#fff);border:1px solid var(--dsw-alias-border-l1,rgba(0,0,0,.1));border-radius:14px;box-shadow:0 8px 32px rgba(0,0,0,.14);display:flex;flex-direction:column;overflow:hidden;z-index:1000}",
      "#" + NS + "-panel[data-hidden='true']{display:none}",
      "#" + NS + "-panel header{display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid var(--dsw-alias-border-l1,rgba(0,0,0,.08))}",
      "#" + NS + "-panel header .title{flex:1;font-size:13px;font-weight:600}",
      "#" + NS + "-panel header .close-btn{cursor:pointer;border:none;background:none;font-size:16px;padding:2px 6px;border-radius:6px;opacity:.6}",
      "#" + NS + "-panel header .close-btn:hover{opacity:1;background:rgba(0,0,0,.06)}",
      "#" + NS + "-history{flex:1;overflow-y:auto;padding:8px 12px;display:flex;flex-direction:column;gap:6px;min-height:120px;max-height:280px}",
      "#" + NS + "-history .msg{max-width:85%;padding:6px 10px;border-radius:10px;font-size:12.5px;line-height:1.5;word-break:break-word}",
      "#" + NS + "-history .msg.user{align-self:flex-end;background:var(--dsw-alias-state-business-primary,#6d8dff);color:#fff}",
      "#" + NS + "-history .msg.assistant{align-self:flex-start;background:var(--dsw-alias-interactive-bg-hover-solid,rgba(0,0,0,.06));color:inherit}",
      "#" + NS + "-history .msg.system{align-self:center;background:transparent;font-size:11px;opacity:.5;padding:2px 6px}",
      "#" + NS + "-input-row{display:flex;align-items:center;gap:6px;padding:8px 12px;border-top:1px solid var(--dsw-alias-border-l1,rgba(0,0,0,.08))}",
      "#" + NS + "-input-row input{flex:1;border:1px solid var(--dsw-alias-border-l2,rgba(0,0,0,.12));border-radius:8px;padding:6px 10px;font-size:12.5px;outline:none;background:transparent;color:inherit}",
      "#" + NS + "-input-row input:focus{border-color:var(--dsw-alias-state-business-primary,#6d8dff)}",
      "#" + NS + "-input-row button{border:none;background:var(--dsw-alias-state-business-primary,#6d8dff);color:#fff;border-radius:8px;padding:6px 10px;cursor:pointer;font-size:12px;white-space:nowrap}",
      "#" + NS + "-input-row button.mic{background:transparent;color:inherit;border:1px solid var(--dsw-alias-border-l2,rgba(0,0,0,.12));padding:6px 8px}",
      "#" + NS + "-input-row button.mic[data-active='true']{background:#ef4444;color:#fff;border-color:#ef4444}",
      "#" + NS + "-status-bar{padding:4px 12px;font-size:11px;opacity:.6;border-top:1px solid var(--dsw-alias-border-l1,rgba(0,0,0,.06));display:flex;justify-content:space-between}",
      "#" + NS + "-task-summary{padding:6px 12px;border-top:1px solid var(--dsw-alias-border-l1,rgba(0,0,0,.06));font-size:11.5px}",
      "#" + NS + "-task-summary .task-count{opacity:.7}",
      "#" + NS + "-task-summary .progress-bar{height:4px;border-radius:2px;background:var(--dsw-alias-border-l2,rgba(0,0,0,.1));margin-top:4px;overflow:hidden}",
      "#" + NS + "-task-summary .progress-fill{height:100%;background:var(--dsw-alias-state-business-primary,#6d8dff);border-radius:2px;transition:width .3s ease}",
      "@media (prefers-reduced-motion:reduce){#" + NS + "-body{animation:none!important}}",
    ].join("\n");
    var tag = document.createElement("style");
    tag.id = NS + "-styles";
    tag.textContent = css;
    document.head.appendChild(tag);
  }

  // ── 创建 DOM ──
  function createDOM() {
    root = document.createElement("div");
    root.id = NS + "-root";
    root.setAttribute("data-mood", "idle");
    root.setAttribute("data-hidden", "false");
    root.style.setProperty(NS + "-scale", String(_scale));

    // 气泡
    bubble = document.createElement("div");
    bubble.id = NS + "-bubble";

    // 宠物本体
    petBody = document.createElement("div");
    petBody.id = NS + "-body";
    petBody.title = _name + " — 点击对话，双击隐藏";
    petBody.setAttribute("role", "button");
    petBody.setAttribute("aria-label", _name);

    // 对话面板
    panel = document.createElement("div");
    panel.id = NS + "-panel";
    panel.setAttribute("data-hidden", "true");
    panel.innerHTML =
      '<header>' +
        '<span class="title">' + escHtml(_name) + '</span>' +
        '<button class="close-btn" title="关闭" aria-label="关闭面板">&times;</button>' +
      '</header>' +
      '<div id="' + NS + '-history"></div>' +
      '<div id="' + NS + '-task-summary" class="task-summary">' +
        '<span class="task-count">任务: 0</span>' +
        '<div class="progress-bar"><div class="progress-fill" style="width:0%"></div></div>' +
      '</div>' +
      '<div id="' + NS + '-cam-wrap" style="display:none;">' +
        '<video id="' + NS + '-cam-video" autoplay playsinline muted style="width:100%;border-radius:8px;background:#000;"></video>' +
        '<div style="font-size:11px;opacity:.7;margin:2px 0 6px;">摄像头已开启——仅在你点「看」或说「看一下」时抓一帧发给模型；关闭面板即断开。</div>' +
      '</div>' +
      '<div id="' + NS + '-status-bar">' +
        '<span id="' + NS + '-status-text">就绪</span>' +
        '<span id="' + NS + '-mode-label">反馈模式</span>' +
      '</div>' +
      '<div class="' + NS + '-input-row">' +
        '<button class="mic" id="' + NS + '-cam-btn" title="摄像头：腾不出手时让小龙看（点「看」或说「看一下」才抓一帧）" aria-label="摄像头">看</button>' +
        '<button class="mic" id="' + NS + '-mic-btn" title="语音输入" aria-label="语音输入">' +
          '<span data-icon="voice" data-size="14"></span>' +
        '</button>' +
        '<input type="text" id="' + NS + '-text-input" placeholder="输入消息，或点击麦克风说话..." />' +
        '<button id="' + NS + '-send-btn">发送</button>' +
      '</div>';

    root.appendChild(bubble);
    root.appendChild(petBody);
    root.appendChild(panel);
    var container = document.getElementById("app") || document.body;
    container.appendChild(root);

    // 缓存引用
    chatHistory = document.getElementById(NS + "-history");
    textInput = document.getElementById(NS + "-text-input");
    micBtn = document.getElementById(NS + "-mic-btn");
    camBtn = document.getElementById(NS + "-cam-btn");
    camVideo = document.getElementById(NS + "-cam-video");
    statusEl = document.getElementById(NS + "-status-text");

    // 绑定面板内事件
    panel.querySelector(".close-btn").addEventListener("click", closePanel);
    document.getElementById(NS + "-send-btn").addEventListener("click", handleTextSend);
    textInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleTextSend(); }
    });
    micBtn.addEventListener("click", toggleVoiceInput);
    if (camBtn) camBtn.addEventListener("click", toggleCam);
  }

  // ── 事件绑定 ──
  function bindEvents() {
    // 拖拽 + 点击抚摸 + 双击隐藏
    petBody.addEventListener("pointerdown", onPointerDown);
    petBody.addEventListener("dblclick", function (e) {
      e.stopPropagation();
      hidePet();
    });
  }

  function onPointerDown(e) {
    if (e.button !== 0) return;
    _dragStart = { x: e.clientX, y: e.clientY };
    _dragMoved = false;
    var base = _pos || { r: 24, b: 24 };
    var onMove = function (ev) {
      var dx = ev.clientX - _dragStart.x;
      var dy = ev.clientY - _dragStart.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) _dragMoved = true;
      if (_dragMoved) {
        _pos = {
          r: clamp(base.r - dx, 4, Math.max(4, window.innerWidth - 160)),
          b: clamp(base.b - dy, 4, Math.max(4, window.innerHeight - 200)),
        };
        root.style.right = _pos.r + "px";
        root.style.bottom = _pos.b + "px";
      }
    };
    var onUp = function () {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      if (_dragMoved) {
        savePos(_pos);
      } else {
        togglePanel();
      }
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
  }

  // ── 面板开关 ──
  function togglePanel() {
    if (!panel) return;
    var hidden = panel.getAttribute("data-hidden") === "true";
    panel.setAttribute("data-hidden", hidden ? "false" : "true");
    if (!hidden) {
      // 关闭时清空草稿
    } else {
      // 打开时滚动到底部
      scrollHistoryToBottom();
      loadConversations();
    }
  }

  function closePanel() {
    if (!panel) return;
    panel.setAttribute("data-hidden", "true");
    stopCam();   // v8.36：关面板即断开摄像头流（隐私默认收窄）
  }

  // ── 宠物显隐 ──
  function hidePet() {
    if (!root) return;
    _visible = false;
    root.setAttribute("data-hidden", "true");
    apiPostConfig({ visible: false }).catch(function () {});
  }

  function showPet() {
    if (!root) return;
    _visible = true;
    root.setAttribute("data-hidden", "false");
    apiPostConfig({ visible: true }).catch(function () {});
  }

  // ── 渲染宠物 ──
  function renderPet() {
    if (!_visible) {
      root.setAttribute("data-hidden", "true");
    } else {
      root.setAttribute("data-hidden", "false");
    }
    root.style.right = (_pos ? _pos.r : 24) + "px";
    root.style.bottom = (_pos ? _pos.b : 24) + "px";
    root.style.setProperty(NS + "-scale", String(_scale));
    petBody.innerHTML = SPRITE_SVG[_sprite] || SPRITE_SVG.custom;
    petBody.title = _name + " — 点击对话，双击隐藏";
    var panelTitle = panel.querySelector("header .title");
    if (panelTitle) panelTitle.textContent = _name;
  }

  // ── 心情 → 气泡 ──
  function updateMood(mood) {
    _state = mood;
    root.setAttribute("data-mood", mood);
    bubble.textContent = MOOD_BUBBLE[mood] || "";
    if (statusEl) {
      var labels = { idle: "就绪", listening: "在听...", thinking: "思考中...", talking: "在说...", working: "执行中...", happy: "完成!", sad: "出错了", waiting: "等待确认", pet: "\u2661" };
      statusEl.textContent = labels[mood] || mood;
    }
  }

  // ── 对话历史渲染 ──
  function renderConversations() {
    if (!chatHistory) return;
    chatHistory.innerHTML = "";
    for (var i = 0; i < _conversations.length; i++) {
      var msg = _conversations[i];
      var div = document.createElement("div");
      div.className = "msg " + (msg.role || "assistant");
      div.textContent = msg.content;
      chatHistory.appendChild(div);
    }
    scrollHistoryToBottom();
  }

  function scrollHistoryToBottom() {
    if (chatHistory) chatHistory.scrollTop = chatHistory.scrollHeight;
  }

  function appendMessage(role, content) {
    _conversations.push({ role: role, content: content, ts: Date.now() });
    // 有界
    if (_conversations.length > 200) _conversations = _conversations.slice(-200);
    renderConversations();
  }

  // ── 任务进度渲染 ──
  function renderTaskProgress(data) {
    var summary = data.summary || data || {};
    var el = document.getElementById(NS + "-task-summary");
    if (!el) return;
    // v8.35：数字归一后再进 innerHTML——防被篡改/异常响应夹带字符串（FreqErr #152 防御面）
    var pending = Number(summary.pending) || 0;
    var done = Number(summary.done) || 0;
    var total = Number(summary.total) || 0;
    var avg = Number(summary.avgProgress) || 0;
    el.innerHTML =
      '<span class="task-count">任务: ' + pending + ' 待办 / ' + done + ' 完成 / ' + total + ' 总计</span>' +
      '<div class="progress-bar"><div class="progress-fill" style="width:' + avg + '%"></div></div>';
  }

  // ── 文本发送 ──
  function handleTextSend() {
    var text = textInput.value.trim();
    if (!text) return;
    textInput.value = "";
    appendMessage("user", text);
    apiPostConversation("user", text).catch(function () {});
    processInput(text);
  }

  // ── 语音输入 ──
  function toggleVoiceInput() {
    if (window._voicePetListening) {
      stopVoiceInput();
    } else {
      startVoiceInput();
    }
  }

  function startVoiceInput() {
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      appendMessage("system", "当前浏览器不支持语音识别，请使用文本输入。");
      return;
    }
    try {
      var rec = new SR();
      rec.lang = "zh-CN";
      rec.continuous = false;
      rec.interimResults = true;
      window._voicePetRec = rec;
      window._voicePetListening = true;
      micBtn.setAttribute("data-active", "true");
      updateMood("listening");
      rec.onresult = function (event) {
        var transcript = "";
        var isFinal = false;
        for (var i = event.resultIndex; i < event.results.length; i++) {
          transcript += event.results[i][0].transcript;
          if (event.results[i].isFinal) isFinal = true;
        }
        if (isFinal && transcript.trim()) {
          appendMessage("user", transcript.trim());
          apiPostConversation("user", transcript.trim()).catch(function () {});
          processInput(transcript.trim());
        }
      };
      rec.onerror = function (e) {
        console.warn("[voice-pet] STT error:", e.error);
        stopVoiceInput();
      };
      rec.onend = function () {
        stopVoiceInput();
      };
      rec.start();
    } catch (e) {
      console.warn("[voice-pet] startVoiceInput failed:", e);
      stopVoiceInput();
    }
  }

  function stopVoiceInput() {
    if (window._voicePetRec) {
      try { window._voicePetRec.stop(); } catch (e) {}
      window._voicePetRec = null;
    }
    window._voicePetListening = false;
    if (micBtn) micBtn.setAttribute("data-active", "false");
    if (_state === "listening") updateMood("idle");
  }

  // ── v8.36 摄像头交互（硬件短接/烧录等腾不出手场景）──
  // 隐私边界：流只在用户显式点「看」后建立；帧仅在用户点「看」或说「看一下」时抓取一张；
  // 关闭面板即断开流。不上传任何画面，除非当次抓帧。
  function toggleCam() {
    if (camStream) { stopCam(); return; }
    startCam();
  }

  async function startCam() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      appendMessage("system", "当前浏览器不支持摄像头（需 HTTPS 或 localhost 环境）。");
      return;
    }
    try {
      camStream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 } }, audio: false });
      if (camVideo) {
        camVideo.srcObject = camStream;
        camVideo.style.display = "block";
      }
      var w = document.getElementById(NS + "-cam-wrap");
      if (w) w.style.display = "block";
      if (camBtn) camBtn.setAttribute("data-active", "true");
      appendMessage("system", "摄像头已开启：点「看」按钮或对我说「看一下」，我就看一眼画面。");
    } catch (e) {
      appendMessage("system", "摄像头打开失败: " + ((e && e.message) || e) + "（需浏览器授权，且页面为 HTTPS/localhost）");
    }
  }

  function stopCam() {
    if (camStream) {
      camStream.getTracks().forEach(function (t) { t.stop(); });
      camStream = null;
    }
    if (camVideo) camVideo.srcObject = null;
    var w = document.getElementById(NS + "-cam-wrap");
    if (w) w.style.display = "none";
    if (camBtn) camBtn.setAttribute("data-active", "false");
  }

  function captureFrame() {
    if (!camStream || !camVideo || !camVideo.videoWidth) return null;
    var maxW = 768;   // 控制帧体积：768 宽 JPEG q0.7 通常 < 200KB
    var scale = Math.min(1, maxW / camVideo.videoWidth);
    var c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(camVideo.videoWidth * scale));
    c.height = Math.max(1, Math.round(camVideo.videoHeight * scale));
    c.getContext("2d").drawImage(camVideo, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.7);
  }

  function lookAndProcess(userText) {
    var frame = captureFrame();
    if (!frame) {
      appendMessage("system", "摄像头未开启：先点「看」按钮打开摄像头。");
      return;
    }
    // v8.37：对话流里补用户侧消息——否则回复突然出现，用户不确定小龙看的是哪一帧
    appendMessage("user", "[摄像头] 已发送当前画面" + (userText ? "：" + userText : ""));
    apiPostConversation("user", "（摄像头画面）" + (userText || "看一下当前画面")).catch(function () {});
    updateMood("thinking");
    var sys = "你是 DeverAI 悬浮助手「小龙」的视觉通道。用户正腾不出手打字（典型：硬件短接/烧录/接线操作中）。" +
      "根据画面与用户的话判断现状，给出简短结论；若用户想要的操作条件已在画面中就绪，最后一句明确写「条件就绪，可以执行」。" +
      "画面可能不清晰：看不清就直说看不清，禁止编造。";
    var msg = { role: "user", content: [
      { type: "text", text: (userText || "看一下现在的画面") },
      { type: "image_url", image_url: { url: frame } },
    ] };
    llmChat({ messages: [{ role: "system", content: sys }, msg], stream: false, temperature: 0.2, max_tokens: 600 })
      .then(function (res) {
        var reply = (res && (res.text || res.content)) || "（视觉模型无回复）";
        appendMessage("assistant", reply);
        apiPostConversation("assistant", reply).catch(function () {});
        if (App.config.ENABLE_VOICE_ASSISTANT && "speechSynthesis" in window) speak(reply);
        updateMood("happy");
        // 视觉确认就绪 → 走既有决策链（可进 agent_loop 用工具真正执行，如开始烧录）
        if (/条件就绪|可以执行/.test(reply)) {
          decideAndProcess((userText ? "用户说：" + userText + "。" : "") + "【小龙看到画面】" + reply);
        }
        loadProgress();
      })
      .catch(function (e) {
        appendMessage("assistant", "看画面失败: " + ((e && e.message) || e) + "（看图需要支持视觉的模型）");
        updateMood("sad");
      });
  }

  // ── 处理输入（统一入口） ──
  function processInput(text) {
    updateMood("thinking");
    var intent = parseIntent(text);
    if (intent) {
      executeIntent(intent, text).then(function (result) {
        if (result && result.message) {
          appendMessage("assistant", result.message);
          apiPostConversation("assistant", result.message).catch(function () {});
          if (App.config.ENABLE_VOICE_ASSISTANT && "speechSynthesis" in window) {
            speak(result.message);
          }
        }
        updateMood(result && result.ok ? "happy" : "sad");
        // 刷新任务进度
        loadProgress();
      }).catch(function (err) {
        var msg = "处理出错: " + (err && err.message ? err.message : String(err));
        appendMessage("assistant", msg);
        updateMood("sad");
        loadProgress();
      });
    } else {
      // v8.18：无匹配意图 → 一次 LLM 调用自判模式（chat / agent_loop / system_expert）
      decideAndProcess(text);
    }
  }

  // ── 意图解析（增强版） ──
  function parseIntent(text) {
    var patterns = [
      { id: "read_status", regex: [/读取状态/, /任务多少/, /查看任务/, /进度如何/, /在做什么/, /当前状态/, /status/, /progress/, /list tasks/] },
      { id: "look", regex: [/看一下/, /看一眼/, /看画面/, /拍(一)?张/, /拍个照/, /拍照/, /打开摄像头/, /开摄像头/, /open camera/i] },
      { id: "add_task", regex: [/添加任务[：:]\s*(.+)/, /新建任务[：:]\s*(.+)/, /插入任务[：:]\s*(.+)/, /add task[:\s]+(.+)/i, /new task[:\s]+(.+)/i], capture: true },
      { id: "complete_task", regex: [/完成(第?[一二三四五12345]?)个?任务/, /完成任务/, /done/, /finish/, /complete\s+(.+)/i], capture: true },
      { id: "delete_task", regex: [/删除任务[：:]\s*(.+)/, /移除任务[：:]\s*(.+)/, /delete task[:\s]+(.+)/i], capture: true },
      { id: "remember", regex: [/记住[：:]\s*(.+)/, /注意[：:]\s*(.+)/, /remember[:\s]+(.+)/i, /note[:\s]+(.+)/i], capture: true },
      { id: "add_constraint", regex: [/不要\s*(.+)/, /禁止\s*(.+)/, /don'?t\s+(.+)/i, /never\s+(.+)/i], capture: true },
      { id: "continue_task", regex: [/继续/, /下一条/, /下一个/, /continue/, /next/] },
      { id: "inject_message", regex: [/告诉Agent[：:]\s*(.+)/, /注入消息[：:]\s*(.+)/, /插入消息[：:]\s*(.+)/, /send to agent[:\s]+(.+)/i, /inject[:\s]+(.+)/i], capture: true },
      { id: "execute_task", regex: [/执行\s*(.+)/, /开始\s*(.+)/, /execute\s+(.+)/i, /start\s+(.+)/i, /run\s+(.+)/i], capture: true },
      { id: "query_progress", regex: [/进度/, /进行到哪/, /做到哪/, /当前进度/, /progress/] },
      { id: "clear_history", regex: [/清空对话/, /清除历史/, /clear history/, /clear conversation/] },
      { id: "show_pet", regex: [/显示小龙/, /唤出小龙/, /show pet/] },
      { id: "hide_pet", regex: [/隐藏小龙/, /收起小龙/, /hide pet/] },
    ];
    for (var i = 0; i < patterns.length; i++) {
      for (var j = 0; j < patterns[i].regex.length; j++) {
        var m = text.match(patterns[i].regex[j]);
        if (m) {
          return {
            id: patterns[i].id,
            capture: patterns[i].capture ? (m[1] || "") : null,
            raw: text,
          };
        }
      }
    }
    return null;
  }

  // ── 执行意图 ──
  async function executeIntent(intent, rawText) {
    switch (intent.id) {
      case "look": {
        // v8.36：抓一帧 → 视觉模型 → 就绪则自动进决策链（可接 agent_loop 真正执行）
        lookAndProcess(intent.raw || "");
        return { ok: true, message: "" };
      }
      case "read_status":
      case "query_progress": {
        var prog = await apiGetJSON("/api/bridge/voice-pet/progress");
        if (prog && prog.summary) {
          var s = prog.summary;
          var msg = "当前共 " + s.total + " 个任务：待办 " + s.pending + " 个，已完成 " + s.done + " 个";
          if (s.avgProgress !== undefined) msg += "，平均进度 " + s.avgProgress + "%";
          if (s.nextTask) msg += "。下一个优先任务：" + s.nextTask.title;
          return { ok: true, message: msg };
        }
        return { ok: true, message: "暂无任务数据。" };
      }
      case "add_task": {
        var title = intent.capture || "新任务";
        await apiPostJSON("/api/bridge/voice-pet/tasks", { title: title });
        return { ok: true, message: "已添加任务：" + title };
      }
      case "complete_task": {
        // v8.14：apiPostJSON 失败统一返回 null（如功能未开启 403），判空防 TypeError 卡死 thinking 态
        var cr = await apiPostJSON("/api/bridge/voice-pet/tasks/continue", {});
        if (!cr) return { ok: false, message: "任务服务不可用。" };
        return { ok: cr.ok, message: cr.message || "已继续下一个任务。" };
      }
      case "delete_task": {
        // 先获取任务列表，按标题匹配
        var tasksData = await apiGetJSON("/api/bridge/voice-pet/tasks");
        var tasks = (tasksData && tasksData.tasks) || [];
        var targetTitle = intent.capture || "";
        var found = null;
        for (var i = 0; i < tasks.length; i++) {
          if (tasks[i].title === targetTitle || tasks[i].id === targetTitle) { found = tasks[i]; break; }
        }
        if (found) {
          await apiDelete("/api/bridge/voice-pet/tasks/" + found.id);
          return { ok: true, message: "已删除任务：" + found.title };
        }
        return { ok: false, message: "未找到匹配的任务：" + targetTitle };
      }
      case "remember": {
        var text = intent.capture || "";
        await apiPostJSON("/api/bridge/voice/notepad", { text: text });
        return { ok: true, message: "已记住：" + text };
      }
      case "add_constraint": {
        var ct = intent.capture || "";
        await apiPostJSON("/api/bridge/voice/constraints", { text: ct });
        return { ok: true, message: "已添加限制条件：" + ct };
      }
      case "continue_task": {
        var cont = await apiPostJSON("/api/bridge/voice-pet/tasks/continue", {});
        if (!cont) return { ok: false, message: "任务服务不可用。" };
        return { ok: cont.ok, message: cont.message || "已继续下一个任务。" };
      }
      case "inject_message": {
        var msg = intent.capture || "";
        // 注入主对话
        injectToMainChat(msg);
        return { ok: true, message: "已注入消息到主对话：" + msg };
      }
      case "execute_task": {
        var task = intent.capture || "";
        injectToMainChat(task);
        return { ok: true, message: "已触发 Agent 执行：" + task };
      }
      case "clear_history": {
        await apiDelete("/api/bridge/voice-pet/conversations");
        _conversations = [];
        renderConversations();
        return { ok: true, message: "对话历史已清空。" };
      }
      case "show_pet":
        showPet();
        return { ok: true, message: "小龙已出现!" };
      case "hide_pet":
        hidePet();
        return { ok: true, message: "小龙已隐藏。" };
      default:
        return { ok: false, error: "未知意图" };
    }
  }

  // ── 反馈模式 ──
  async function feedbackMode(text) {
    if (typeof llmChat !== "function") {
      return { ok: false, text: "LLM 客户端不可用" };
    }
    try {
      var cfg = App.config || {};
      var resp = await llmChat({
        messages: [
          { role: "system", content: buildSystemPrompt() },
          { role: "user", content: text },
        ],
        model: cfg.model || "",
        temperature: 0.3,
        max_tokens: 1024,
      });
      return { ok: true, text: resp.text || resp.content || "" };
    } catch (e) {
      return { ok: false, text: "LLM 调用失败: " + (e.message || "") };
    }
  }

  function buildSystemPrompt() {
    return "你是 DeverAI 语音助手「小龙」，一个运行在浏览器中的 AI 助手。\n" +
      "回答要求：简洁、自然、口语化，适合语音回复（不超过 3 句话）。\n" +
      "当前工作区文件操作通过工具完成，不要编造文件内容。\n" +
      "你可以：查询任务进度、添加任务、注入消息到主对话、执行任务。";
  }

  // ---- v8.18 模式判定 + 悬浮助手 Agent 循环 ----
  var PET_AGENT_SYSTEM = "你是 DeverAI 悬浮助手，现在进入自主 Agent 循环。你可以调用工作区全部工具（命令执行/文件读写/资产检索/子Agent委派）完成任务。"
    + " 当前工作区上下文由工具结果注入；默认单打独斗，需要时也可 delegate_task 委派专家。"
    + " 任务完成或需要用户决策时输出明确结论。";
  var SYSTEM_EXPERT_PROMPT = "你是 DeverAI 系统专家，拥有系统知识与设计上下文的完整访问权。"
    + " 你可以读取工作区文档（Design.md/Techniques.md/Fact.md/FreqErr.md）来回答关于 DeverAI 架构、模块、开关、设计决策的问题。"
    + " 回答要准确、引用具体文档章节；只读不写，不要修改任何文件；不确定的说不确定。";

  function _petHistory() {
    var h = [];
    var conv = _conversations.slice(-12);
    for (var i = 0; i < conv.length; i++) {
      if (conv[i].role === "user" || conv[i].role === "assistant") {
        h.push({ role: conv[i].role, content: conv[i].content });
      }
    }
    return h;
  }

  function renderPetEvent(ev) {
    if (!ev || !ev.type) return;
    if (ev.type === "tool_start") {
      appendMessage("assistant", "▶ " + (ev.name || "tool"));
    } else if (ev.type === "tool_result") {
      appendMessage("assistant", "✓ " + (ev.name || "tool") + (ev.ok ? "" : "（失败）"));
    } else if (ev.type === "text_delta" && ev.content) {
      appendMessage("assistant", ev.content);
    } else if (ev.type === "run_done") {
      appendMessage("assistant", "（Agent 循环结束，共 " + (ev.rounds || 0) + " 轮）");
    } else if (ev.type === "run_cancelled") {
      appendMessage("assistant", "（已停止）");
    } else if (ev.type === "run_error") {
      appendMessage("assistant", "（出错：" + (ev.message || "未知") + "）");
    }
  }

  async function runPetLoop(text) {
    // 暂停主循环（如果正在运行）——软暂停，当前工具跑完即停靠
    var pausedMain = false;
    if (typeof Agent !== "undefined" && Agent.running && typeof agentPauseMain === "function") {
      agentPauseMain();
      pausedMain = true;
    }
    updateMood("thinking");
    appendMessage("assistant", "已进入 Agent 循环，正在自主执行…");
    try {
      var res = await runPetAgent(text, {
        history: _petHistory(),
        systemPrompt: PET_AGENT_SYSTEM,
        maxRounds: 12,
        onEvent: renderPetEvent,
      });
      if (res && res.text) {
        appendMessage("assistant", res.text);
        apiPostConversation("assistant", res.text).catch(function () {});
      }
    } catch (e) {
      appendMessage("assistant", "Agent 循环异常: " + (e && e.message ? e.message : String(e)));
    } finally {
      if (pausedMain && typeof agentResumeMain === "function") agentResumeMain();
      loadProgress();
    }
  }

  async function runPetExpert(text) {
    updateMood("thinking");
    appendMessage("assistant", "正在咨询系统专家…");
    try {
      var res = await runPetAgent(text, {
        history: _petHistory(),
        systemPrompt: SYSTEM_EXPERT_PROMPT,
        maxRounds: 6,
        onEvent: renderPetEvent,
      });
      if (res && res.text) {
        appendMessage("assistant", res.text);
        apiPostConversation("assistant", res.text).catch(function () {});
      } else {
        appendMessage("assistant", "专家暂无回复。");
      }
    } catch (e) {
      appendMessage("assistant", "专家调用失败: " + (e && e.message ? e.message : String(e)));
    } finally {
      loadProgress();
    }
  }

  async function decideAndProcess(text) {
    if (typeof llmChat !== "function") {
      appendMessage("assistant", "LLM 客户端不可用");
      updateMood("sad");
      return;
    }
    updateMood("thinking");
    try {
      // 取任务摘要供 chat 模式直接回答
      var prog = await apiGetJSON("/api/bridge/voice-pet/progress");
      var summary = (prog && prog.summary) ? prog.summary : null;
      var ctxLine = summary
        ? ("当前任务摘要：共 " + summary.total + " 个，待办 " + summary.pending + "，完成 " + summary.done
           + (summary.nextTask ? "；下一优先：" + summary.nextTask.title : "") + "。")
        : "暂无任务摘要。";
      var sysPrompt = "你是 DeverAI 悬浮助手「小龙」。用户发来一条消息，先判断该用哪种模式处理，再直接给出该模式的输出。\n"
        + "三种模式：\n"
        + "- chat：单轮跑腿。闲聊/简单查询/不需要工具。直接在 reply 中回答（可引用下方[任务摘要]）。\n"
        + "- agent_loop：需要动手干活（刷固件、编译、调试、多步骤）。reply 简要告知打算做什么，然后进入 Agent 循环自主执行。\n"
        + "- system_expert：问的是 DeverAI 系统本身的架构/设计/开关/模块。进入专家模式查阅 Design.md/Techniques.md 后回答。\n"
        + "[任务摘要]\n" + ctxLine + "\n"
        + "严格输出一行 JSON 作为第一行：{\"mode\":\"chat\"|\"agent_loop\"|\"system_expert\",\"reply\":\"...\"}\n"
        + "chat 模式可追加自然语言回复；agent_loop/system_expert 的 reply 只是一句简短告知。";
      var resp = await llmChat({
        messages: [
          { role: "system", content: sysPrompt },
          { role: "user", content: text },
        ],
        stream: false,
        temperature: 0.1,
        max_tokens: 800,
      });
      var raw = resp.text || resp.content || "";
      var parsed = null;
      try {
        var m = raw.match(/^\s*(\{[\s\S]*\})/);
        if (m) parsed = JSON.parse(m[1]);
      } catch (e) { parsed = null; }
      if (!parsed || !parsed.mode) {
        // 解析失败退化 chat
        appendMessage("assistant", raw.trim() || "抱歉，我无法处理这个请求。");
        apiPostConversation("assistant", raw.trim()).catch(function () {});
        updateMood("happy");
        loadProgress();
        return;
      }
      if (parsed.mode === "agent_loop") {
        if (parsed.reply) {
          appendMessage("assistant", parsed.reply);
          apiPostConversation("assistant", parsed.reply).catch(function () {});
        }
        await runPetLoop(text);
        updateMood("happy");
      } else if (parsed.mode === "system_expert") {
        await runPetExpert(text);
        updateMood("happy");
      } else {
        var reply = parsed.reply || raw.trim() || "抱歉，我无法处理这个请求。";
        appendMessage("assistant", reply);
        apiPostConversation("assistant", reply).catch(function () {});
        if (App.config.ENABLE_VOICE_ASSISTANT && "speechSynthesis" in window) speak(reply);
        updateMood("happy");
        loadProgress();
      }
    } catch (e) {
      appendMessage("assistant", "处理出错: " + (e && e.message ? e.message : String(e)));
      updateMood("sad");
      loadProgress();
    }
  }

  // ── 注入主对话 ──
  function injectToMainChat(text) {
    var input = document.getElementById("chat-input");
    var sendBtn = document.getElementById("btn-send");
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
    if (sendBtn) sendBtn.click();
  }

  // ── TTS ──
  function speak(text) {
    if (!("speechSynthesis" in window) || !text) return;
    try {
      window.speechSynthesis.cancel();
      var utter = new SpeechSynthesisUtterance(text);
      utter.lang = "zh-CN";
      utter.rate = 1.0;
      var voices = window.speechSynthesis.getVoices();
      var match = voices.find(function (v) { return v.lang === "zh-CN"; }) || voices.find(function (v) { return v.lang.startsWith("zh"); });
      if (match) utter.voice = match;
      utter.onstart = function () { updateMood("talking"); };
      utter.onend = function () { updateMood("idle"); };
      utter.onerror = function () { updateMood("idle"); };
      window.speechSynthesis.speak(utter);
    } catch (e) {
      console.warn("[voice-pet] TTS error:", e);
    }
  }

  // ── API 调用 ──
  function _handleResponse(resp) {
    if (!resp.ok) {
      console.warn('[voice-pet] API HTTP error:', resp.status);
      return Promise.reject(new Error('HTTP ' + resp.status));
    }
    return resp.json().catch(function () { return null; });
  }

  function apiGetJSON(path) {
    return fetch(path, { cache: 'no-store', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' } })
      .then(_handleResponse)
      .catch(function (e) { console.warn('[voice-pet] API GET', path, 'failed:', e); return null; });
  }

  function apiPostJSON(path, body) {
    return fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      cache: 'no-store',
      credentials: 'same-origin',
    }).then(_handleResponse)
      .catch(function (e) { console.warn('[voice-pet] API POST', path, 'failed:', e); return null; });
  }

  function apiPostConfig(body) {
    return apiPostJSON('/api/bridge/voice-pet/config', body);
  }

  function apiPostConversation(role, content) {
    return apiPostJSON('/api/bridge/voice-pet/conversations', { role: role, content: content });
  }

  function apiDelete(path) {
    return fetch(path, { method: 'DELETE', cache: 'no-store', credentials: 'same-origin' })
      .then(_handleResponse)
      .catch(function (e) { console.warn('[voice-pet] API DELETE', path, 'failed:', e); return null; });
  }

  // ── 数据加载 ──
  function loadConfig() {
    apiGetJSON("/api/bridge/voice-pet/config").then(function (data) {
      if (data && data.config) {
        var cfg = data.config;
        if (cfg.name) _name = cfg.name;
        if (cfg.size) _scale = cfg.size;
        if (typeof cfg.visible === "boolean") _visible = cfg.visible;
        if (cfg.conv_mode) _convMode = cfg.conv_mode;
        // v8.14：服务端白名单持久化的键是 builtin_sprite（此前读 mascot_style，
        // 该键服务端永不下发 → 外观设置永不生效）；回退本地 App.config 同名键
        var style = cfg.builtin_sprite || (window.App && App.config && App.config.builtin_sprite) || "";
        if (style && SPRITE_SVG[style]) _sprite = style;
        renderPet();
      }
    });
  }

  function loadConversations() {
    apiGetJSON("/api/bridge/voice-pet/conversations?limit=50").then(function (data) {
      if (data && data.conversations) {
        _conversations = data.conversations.map(function (c) {
          return { role: c.role, content: c.content, ts: c.ts };
        });
        renderConversations();
      }
    });
  }

  function loadProgress() {
    apiGetJSON("/api/bridge/voice-pet/progress").then(function (data) {
      if (data) renderTaskProgress(data);
    });
  }

  // ── 工具函数 ──
  function clamp(v, min, max) { return Math.min(max, Math.max(min, v)); }
  function escHtml(s) { var d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
  function loadPos() {
    try {
      var raw = localStorage.getItem(POS_KEY);
      if (!raw) return null;
      var p = JSON.parse(raw);
      if (typeof p.r === "number" && typeof p.b === "number") return p;
    } catch (e) {}
    return null;
  }
  function savePos(pos) {
    try { localStorage.setItem(POS_KEY, JSON.stringify(pos)); } catch (e) {}
  }

  // ── 公开 API ──
  var _initialized = false;
  window.VoicePet = {
    init: function () {
      // 开关关闭时不初始化；后续在设置页开启后调用 init() 才真正创建 DOM
      if (_initialized || !(App && App.config && App.config.ENABLE_VOICE_ASSISTANT)) return;
      _initialized = true;
      init();
    },
    show: showPet,
    hide: hidePet,
    togglePanel: togglePanel,
    openPanel: function () { if (!panel) return; panel.setAttribute("data-hidden", "false"); loadConversations(); loadProgress(); },
    closePanel: closePanel,
    setMood: updateMood,
    speak: speak,
    appendMessage: appendMessage,
    loadProgress: loadProgress,
    loadConversations: loadConversations,
    injectToMainChat: injectToMainChat,
    get state() { return _state; },
    get name() { return _name; },
    set name(v) { _name = v; renderPet(); },
    get visible() { return _visible; },
  };

  // ── 自动初始化 ──
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      if (App && App.config && App.config.ENABLE_VOICE_ASSISTANT) init();
    });
  } else {
    if (App && App.config && App.config.ENABLE_VOICE_ASSISTANT) init();
  }
})();
