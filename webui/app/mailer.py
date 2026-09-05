"""邮件发送（SMTP SSL）：用于注册验证码。

凭证从环境变量读取，不落盘：
- SMTP_HOST：SMTP 服务器（如 smtp.qq.com）
- SMTP_PORT：端口（默认 465，SSL）
- SMTP_USER：发件邮箱
- SMTP_PASS：授权码（非邮箱密码，QQ/163 等均用授权码）

安全：
- 全程 SSL（smtplib.SMTP_SSL），不走明文 25/587。
- 凭证仅环境变量，绝不写入 data/ 或日志。
- 发送失败不抛敏感信息（如主机名/用户名），只回 False + 泛化原因。
"""
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

_CODE_TTL_SEC = 600  # 验证码 10 分钟过期


def _credentials() -> tuple[str, int, str, str] | None:
    """从环境变量读 SMTP 凭证；缺失返回 None。"""
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    pwd = os.environ.get("SMTP_PASS", "").strip()
    if not host or not user or not pwd:
        return None
    try:
        port = int(os.environ.get("SMTP_PORT", "465"))
    except ValueError:
        port = 465
    return host, port, user, pwd


def is_configured() -> bool:
    """SMTP 凭证是否已配置（供前端提示）。"""
    return _credentials() is not None


def send_text_email(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    """v8.22：发送通用文本邮件（安全中心泄露检查报告等）。

    与验证码邮件同一套 SSL 通道与安全约束；subject/body 长度上限防滥用。
    """
    if not to_email or "\r" in to_email or "\n" in to_email:
        return False, "邮箱地址非法"
    subject = str(subject or "").strip()[:200]
    body = str(body or "")[:100_000]
    if not subject or not body:
        return False, "邮件主题或正文为空"
    # 防邮件头注入：主题禁止换行
    if "\r" in subject or "\n" in subject:
        return False, "邮件主题非法"
    creds = _credentials()
    if creds is None:
        return False, "邮件服务未配置（SMTP_HOST/SMTP_USER/SMTP_PASS 环境变量缺失）"
    host, port, user, _pwd = creds
    msg = MIMEMultipart("alternative")
    msg["From"] = f"DeverAI <{user}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as srv:
            srv.login(user, _pwd)
            srv.sendmail(user, [to_email], msg.as_string())
        return True, ""
    except smtplib.SMTPAuthenticationError:
        return False, "发件邮箱认证失败（请检查 SMTP_USER/SMTP_PASS）"
    except smtplib.SMTPException:
        return False, "邮件发送失败（SMTP 协议错误）"
    except (OSError, ConnectionError):
        return False, "邮件发送失败（网络连接错误）"
    except Exception as e:
        try:
            from .errors import log_error
            log_error("通用邮件发送未知异常", e)
        except Exception:
            pass
        return False, "邮件发送失败（未知错误）"


def send_verification_code(to_email: str, code: str) -> tuple[bool, str]:
    """发送验证码邮件。成功返回 (True, "")，失败返回 (False, 泛化原因)。

    不抛异常、不泄露凭证细节。
    """
    # v6.2 P1-3：防邮件头注入——拒绝含换行的邮箱
    if not to_email or "\r" in to_email or "\n" in to_email:
        return False, "邮箱地址非法"
    creds = _credentials()
    if creds is None:
        return False, "邮件服务未配置（SMTP_HOST/SMTP_USER/SMTP_PASS 环境变量缺失）"
    host, port, user, _pwd = creds
    msg = MIMEMultipart("alternative")
    msg["From"] = f"DeverAI <{user}>"
    msg["To"] = to_email
    msg["Subject"] = f"DeverAI 注册验证码：{code}"
    body = (
        f"你的 DeverAI 注册验证码是：{code}\n\n"
        f"验证码 {_CODE_TTL_SEC // 60} 分钟内有效，请尽快完成注册。\n"
        f"如非本人操作，请忽略此邮件。\n"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))
    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as srv:
            srv.login(user, _pwd)
            srv.sendmail(user, [to_email], msg.as_string())
        return True, ""
    except smtplib.SMTPAuthenticationError:
        return False, "发件邮箱认证失败（请检查 SMTP_USER/SMTP_PASS）"
    except smtplib.SMTPException:
        return False, "邮件发送失败（SMTP 协议错误）"
    except (OSError, ConnectionError):
        return False, "邮件发送失败（网络连接错误）"
    except Exception as e:
        try:
            from .errors import log_error
            log_error("邮件发送未知异常", e)
        except Exception:
            pass
        return False, "邮件发送失败（未知错误）"
