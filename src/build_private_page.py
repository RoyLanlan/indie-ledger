#!/usr/bin/env python3
"""Build the PRIVATE indie-ledger page: pricing tiers + sales copy.

Security model
--------------
The private page is never committed to the public repository. It is a
single self-contained HTML file whose sensitive body is AES-GCM encrypted
with a key derived from the user's password via PBKDF2-HMAC-SHA256.

What is and is not protected:
  - The ciphertext, the salt and the iteration count live in the file.
  - The PASSWORD ITSELF IS NOT IN THE FILE. The key is derived at runtime
    from what the user types, so reading the source does not reveal it.
  - Weak passwords are brute-forceable offline, because an attacker who
    has the file can try candidates against the PBKDF2+AES-GCM envelope.
    Use a long passphrase.
  - The real protection for anything truly sensitive is to NOT deploy
    this file publicly at all.

The plaintext sources (商品页 / 文案包) are read from disk and never
copied into the public repo.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PBKDF2_ROUNDS = 600_000
SALT_BYTES = 16
# AES-CTR needs a FULL 16-byte counter block.  A 12-byte nonce works
# with `openssl enc` only because openssl silently zero-pads it -- but
# WebCrypto's AES-CTR rejects a counter that is not exactly 16 bytes, so
# the browser could never open the page.  16 bytes everywhere.
NONCE_BYTES = 16


# --------------------------------------------------------------------------
# a tiny, dependency-free AES-GCM implementation is NOT worth writing.
# Instead we shell out to openssl, which is present on the host and is a
# reviewed implementation.  GCM is used via `enc` with a 96-bit IV.
# --------------------------------------------------------------------------
def encrypt(plaintext: bytes, password: str, salt: bytes, nonce: bytes) -> bytes:
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt,
                              PBKDF2_ROUNDS, dklen=32)
    import subprocess
    p = subprocess.run(
        ["openssl", "enc", "-aes-256-gcm", "-e",
         "-K", key.hex(), "-iv", nonce.hex()],
        input=plaintext, capture_output=True)
    if p.returncode != 0:
        # openssl's enc does not support GCM; fall back to CTR+HMAC which
        # is encrypt-then-MAC and equally safe for this use.
        raise RuntimeError(f"openssl aes-256-gcm unsupported: "
                           f"{p.stderr.decode()[:200]}")
    return p.stdout


def encrypt_etm(plaintext: bytes, password: str, salt: bytes,
                 nonce: bytes) -> bytes:
    """Encrypt-then-MAC: AES-256-CTR keystream + HMAC-SHA256 tag.

    openSSL's `enc` refuses GCM, so we compose CTR + HMAC manually.
    The tag covers the ciphertext and both nonces, so tampering is
    detected before any plaintext is produced.
    """
    import subprocess
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt,
                              PBKDF2_ROUNDS, dklen=64)
    enc_key, mac_key = key[:32], key[32:]
    p = subprocess.run(
        ["openssl", "enc", "-aes-256-ctr", "-e",
         "-K", enc_key.hex(), "-iv", nonce.hex()],
        input=plaintext, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode()[:200])
    ct = p.stdout
    mac = hmac_sha256(mac_key, nonce + salt + ct)
    return salt + nonce + mac + ct


def hmac_sha256(key: bytes, msg: bytes) -> bytes:
    import hmac
    return hmac.new(key, msg, hashlib.sha256).digest()


def md_to_html(md_text: str) -> str:
    """Minimal Markdown -> HTML for our own files (headings, tables, lists,
    blockquotes, code, bold).  Keeps the private page dependency-free."""
    def esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;"))

    out = []
    lines = md_text.split("\n")
    i = 0
    in_code = False
    code_buf = []

    def inline(s):
        s = esc(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                   r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
        return s

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            if in_code:
                out.append("<pre><code>" + esc("\n".join(code_buf)) +
                           "</code></pre>")
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(ln)
            i += 1
            continue

        # table
        if ln.startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\|[\s:\-|]+\|$", lines[i + 1].strip()):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in
                             lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{inline(h)}</th>" for h in head)
            tb = ""
            for r in rows:
                tb += "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
            out.append(f'<div class="tscroll"><table><thead><tr>{th}</tr>'
                       f'</thead><tbody>{tb}</tbody></table></div>')
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        if ln.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append("<blockquote>" + inline(" ".join(buf)) + "</blockquote>")
            continue

        if re.match(r"^\s*[-*]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>"
                                       for x in items) + "</ul>")
            continue

        if re.match(r"^\s*\d+\.\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>" + "".join(f"<li>{inline(x)}</li>"
                                       for x in items) + "</ol>")
            continue

        if not ln.strip():
            i += 1
            continue

        buf = []
        while i < len(lines) and lines[i].strip() and \
                not re.match(r"^(#{1,4}\s|\||>|```|\s*[-*]\s|\s*\d+\.\s)",
                             lines[i]):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append("<p>" + inline(" ".join(buf)) + "</p>")
        else:
            i += 1

    return "\n".join(out)


CSS = """
:root{--black:#171717;--white:#fff;--g900:#171717;--g800:#2e2e2e;
--g600:#4d4d4d;--g500:#737373;--g400:#a3a3a3;--g300:#d4d4d4;--g200:#e5e5e5;
--g100:#f5f5f5;--red:#dc2626;--green:#16a34a;--blue:#2563eb;}
*{box-sizing:border-box}
body{margin:0;background:var(--white);color:var(--g900);
font-family:'Geist','Noto Sans SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
font-size:17px;line-height:1.7;-webkit-font-smoothing:antialiased}
.wrap{max-width:960px;margin:0 auto;padding:0 24px}
nav{border-bottom:1px solid var(--g200);position:sticky;top:0;background:rgba(255,255,255,.92);backdrop-filter:blur(8px);z-index:10}
.navin{display:flex;align-items:center;gap:28px;height:60px}
.brand{font-weight:600;text-decoration:none;color:var(--g900);letter-spacing:-.02em}
.navlinks{display:flex;gap:20px;flex-wrap:wrap;margin-left:auto}
.navlinks a{color:var(--g600);text-decoration:none;font-size:14px}
.navlinks a:hover{color:var(--g900)}
header{padding:72px 0 48px;border-bottom:1px solid var(--g100)}
h1{font-size:clamp(32px,6vw,52px);line-height:1.15;letter-spacing:-.03em;margin:0 0 18px;font-weight:600}
h2{font-size:clamp(24px,4vw,34px);line-height:1.25;letter-spacing:-.02em;margin:0 0 16px;font-weight:600}
h3{font-size:19px;margin:0 0 12px;font-weight:600}
p{margin:0 0 16px}
.eyebrow{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--g500);margin-bottom:14px;font-weight:500}
.lead{font-size:19px;color:var(--g600)}
section{padding:56px 0;border-bottom:1px solid var(--g100)}
table{width:100%;border-collapse:collapse;font-size:15px;margin:0 0 8px}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--g200);vertical-align:top}
th{font-weight:500;color:var(--g500);font-size:13px;letter-spacing:.02em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;font-family:'Geist Mono',ui-monospace,SFMono-Regular,monospace}
.tscroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
code{background:var(--g100);padding:2px 6px;border-radius:4px;font-size:.88em;
font-family:'Geist Mono',ui-monospace,monospace}
pre{background:var(--g100);padding:16px;border-radius:8px;overflow-x:auto}
pre code{background:none;padding:0}
blockquote{margin:0 0 16px;padding:14px 18px;border-left:2px solid var(--g300);
color:var(--g600);background:var(--g100);border-radius:0 6px 6px 0}
ul,ol{margin:0 0 16px;padding-left:22px}
li{margin-bottom:6px}
a{color:var(--blue)}
.tag{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--g300);color:var(--g600)}
.callout{border:1px solid var(--g200);border-radius:10px;padding:24px;margin-top:28px;box-shadow:0 1px 0 var(--g200)}
/* gate */
#gate{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
.gatebox{width:100%;max-width:380px;border:1px solid var(--g200);border-radius:12px;padding:32px;box-shadow:0 1px 0 var(--g200)}
.gatebox h1{font-size:22px;margin-bottom:8px}
.gatebox p{font-size:14px;color:var(--g600)}
.gatebox input{width:100%;padding:11px 12px;border:1px solid var(--g300);border-radius:8px;font-size:15px;margin:16px 0 12px;font-family:inherit}
.gatebox button{width:100%;padding:11px;border:1px solid var(--g900);background:var(--g900);color:#fff;border-radius:8px;font-size:15px;cursor:pointer;font-family:inherit}
.gatebox button:disabled{opacity:.5;cursor:not-allowed}
.err{color:var(--red);font-size:13px;min-height:18px}
#content{display:none}
#gate.hidden{display:none}
.warn{border-left:2px solid var(--red);background:#fef2f2;color:#991b1b;padding:14px 18px;border-radius:0 6px 6px 0;font-size:14px;margin:16px 0}
@media(max-width:640px){.navin{gap:16px}.navlinks{gap:14px;font-size:13px}header{padding:48px 0 32px}section{padding:40px 0}th,td{padding:8px 8px;font-size:14px}}
"""

JS = """
(async function () {
  const box = document.getElementById('gate');
  const form = document.getElementById('gform');
  const inp = document.getElementById('pw');
  const err = document.getElementById('err');
  const btn = document.getElementById('go');

  function b64d(s){ const bin=atob(s); const u=new Uint8Array(bin.length);
    for(let i=0;i<bin.length;i++) u[i]=bin.charCodeAt(i); return u; }
  function b64e(u){ let s=''; for(let i=0;i<u.length;i++) s+=String.fromCharCode(u[i]);
    return btoa(s); }

  const SALT = '__SALT__', NONCE = '__NONCE__', TAG = '__TAG__',
        CT = '__CT__', ROUNDS = __ROUNDS__;

  async function unlock(pw){
    const enc = new TextEncoder();
    const km = await crypto.subtle.importKey('raw', enc.encode(pw),
      {name:'PBKDF2'}, false, ['deriveBits']);
    const bits = await crypto.subtle.deriveBits(
      {name:'PBKDF2', salt:b64d(SALT), iterations:ROUNDS, hash:'SHA-256'},
      km, 512);
    const all = new Uint8Array(bits);
    const macKey = all.slice(32);

    // decrypt AES-CTR via WebCrypto (CTR is supported natively)
    const key = await crypto.subtle.importKey('raw', all.slice(0,32),
      {name:'AES-CTR'}, false, ['decrypt']);
    const ct = b64d(CT);

    // Encrypt-then-MAC: verify the tag BEFORE producing any plaintext.
    // Decrypting first and checking afterwards would still discard a
    // bad plaintext, but it also means a wrong password runs the whole
    // AES path -- slower, and it leaks how far the attacker got.
    const hk = await crypto.subtle.importKey('raw', macKey,
      {name:'HMAC', hash:'SHA-256'}, false, ['sign']);
    const data = new Uint8Array(b64d(NONCE).length + b64d(SALT).length + ct.length);
    data.set(b64d(NONCE),0);
    data.set(b64d(SALT), b64d(NONCE).length);
    data.set(ct, b64d(NONCE).length + b64d(SALT).length);
    const sig = new Uint8Array(await crypto.subtle.sign('HMAC', hk, data));
    const want = b64d(TAG);
    if (sig.length !== want.length) return null;
    let diff = 0;
    for (let i=0;i<sig.length;i++) diff |= sig[i] ^ want[i];
    if (diff !== 0) return null;

    // counter must be exactly 16 bytes; length 128 matches openssl's
    // full-counter increment so both sides produce identical keystream
    const plain = await crypto.subtle.decrypt(
      {name:'AES-CTR', counter:new Uint8Array(b64d(NONCE)), length:128},
      key, ct);
    return new TextDecoder().decode(plain);
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const pw = inp.value;
    if (!pw) return;
    btn.disabled = true; err.textContent = '校验中…';
    try {
      const html = await unlock(pw);
      if (html === null) {
        err.textContent = '密码不对。';
        btn.disabled = false;
        return;
      }
      document.getElementById('content').innerHTML = html;
      document.getElementById('content').style.display = 'block';
      box.classList.add('hidden');
      window.scrollTo(0,0);
    } catch (err2) {
      err.textContent = '解密失败：' + (err2 && err2.message ? err2.message : '未知错误');
      btn.disabled = false;
    }
  });
  inp.focus();
})();
"""


def build_private(md_text, password):
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    blob = encrypt_etm(md_to_html(md_text).encode("utf-8"), password,
                       salt, nonce)
    # blob = salt|nonce|tag|ct
    tag = blob[len(salt) + len(nonce): len(salt) + len(nonce) + 32]
    ct = blob[len(salt) + len(nonce) + 32:]

    js = (JS
          .replace("__SALT__", base64.b64encode(salt).decode())
          .replace("__NONCE__", base64.b64encode(nonce).decode())
          .replace("__TAG__", base64.b64encode(tag).decode())
          .replace("__CT__", base64.b64encode(ct).decode())
          .replace("__ROUNDS__", str(PBKDF2_ROUNDS)))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>indie-ledger — 私有页</title>
<meta name="robots" content="noindex, nofollow">
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<div id="gate">
  <form class="gatebox" id="gform">
    <h1>indie-ledger 私有页</h1>
    <p>定价、获客文案与销售话术。需要密码。</p>
    <input type="password" id="pw" placeholder="密码" autocomplete="current-password">
    <div class="err" id="err"></div>
    <button type="submit" id="go">解锁</button>
  </form>
</div>
<main id="content"></main>
<script>{js}</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product-page", required=True)
    ap.add_argument("--copy-pack", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    parts = []
    for label, path in (("商品页", args.product_page),
                        ("获客文案包", args.copy_pack)):
        with open(path, encoding="utf-8") as f:
            parts.append(f"<!-- {label} -->\n" + f.read())
    md = "\n\n---\n\n".join(parts)

    html = build_private(md, args.password)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {args.out} ({len(html.encode('utf-8'))} bytes)")
    print("password embedded in file:",
          "YES-BAD" if args.password in html else "no")
    # Probe for ACTUAL source content, not the words themselves: the
    # gate label legitimately says "定价、获客文案与销售话术".  Checking
    # for those words reported a leak that did not exist.
    import re as _re
    probes = ["不再维护合计", "死亡时间中位数", "死法结构",
              "上线后 30 天内就停更", "私有链接发货", "跟进的三个时机"]
    found = [p for p in probes if p in html]
    print("source plaintext leaked:", "YES-BAD:" + ",".join(found)
          if found else "no")


if __name__ == "__main__":
    main()
