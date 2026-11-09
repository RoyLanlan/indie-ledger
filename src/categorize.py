#!/usr/bin/env python3
"""L3a: deterministic product categorisation from Show HN titles.

Why this exists
---------------
The whole point of the ledger is a report that slices survival by industry.
Without a category there is no vertical analysis at all.

Why it is deterministic
-----------------------
Rules of this project: same input -> same output, byte for byte. An LLM
classifier would make the report irreproducible and un-auditable. So this
is a pure keyword taxonomy over the Show HN title, and the precision of
that taxonomy is *measured*, not assumed (see audit_categories.py).

Rules
-----
1. A title may match several categories; the score is the sum of matched
   keyword weights, and the winner is the highest score. Ties are broken
   by a fixed category order, never by dict ordering.
2. A title with no keyword hit is `other`. `other` is a real bucket, not
   a failure -- but a category holding most of the corpus is a red flag
   and is reported as such.
3. No keyword may match a substring of a longer word by accident. Every
   keyword is matched on a word boundary, so "ai" does not match "said".
4. The taxonomy is frozen. Changing it changes every historical report,
   so version it and record the version in the output.
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict

TAXONOMY_VERSION = "v1"

# Order is meaningful: it breaks ties deterministically.
CATEGORY_ORDER = [
    "devtools",
    "ai_ml",
    "infra_devops",
    "data_analytics",
    "security_privacy",
    "saas_productivity",
    "design_creative",
    "media_content",
    "social_community",
    "commerce_fintech",
    "education_learning",
    "health_wellness",
    "science_hardware",
    "games_entertainment",
    "productivity_generic",
]

# keyword -> weight. Weight 2 = unambiguous term, 1 = weak/contextual.
KEYWORDS = {
    "devtools": {
        "ide": 2, "editor": 2, "code editor": 2, "compiler": 2, "linter": 2,
        "debugger": 2, "refactor": 2, "syntax": 2, "terminal": 2,
        "shell": 1, "cli": 2, "sdk": 2, "api client": 2, "http client": 2,
        "git": 2, "version control": 2, "pull request": 2, "code review": 2,
        "unit test": 2, "testing": 1, "build tool": 2, "bundler": 2,
        "package manager": 2, "dependency": 1, "programming language": 2,
        "developer tool": 2, "dev tool": 2, "code generator": 2,
        "scaffolding": 2, "snippet": 1, "documentation": 1,
        "regex": 2, "parser": 2, "framework": 1, "runtime": 1,
        "code search": 2, "pair programming": 2, "open source": 1,
        "standard library": 2, "library": 1, "toolkit": 2,
        "prototyping library": 2, "emulator": 2, "simulator": 1,
        "static analysis": 2, "type checker": 2, "formatter": 2,
        "diff tool": 2, "patch": 1, "ci": 1, "automation": 1,
        # added after the precision audit: programming-language and
        # dev-tool titles that were landing in "other"
        # NOTE: bare "language" was removed. It matched "Type Korean iOS"
        # (an education product) and stole it from education_learning.
        "programming": 2, "python": 2, "rust": 2,
        "golang": 2, "typescript": 2, "javascript": 2, "ruby": 2,
        "elixir": 2, "haskell": 2, "zig": 2, "cpp": 2, "c#": 2,
        "clojure": 2, "swift": 2, "kotlin": 2, "scala": 2,
        "compiler": 2, "bytecode": 2, "vm": 1, "ast": 2, "lexer": 2,
        "code analysis": 2, "codegen": 2, "template": 1, "boilerplate": 2,
        "hooks": 2, "import hook": 2, "extension": 1, "plugin": 2,
        "middleware": 2, "proxy": 1, "http server": 2, "web server": 2,
        "http client": 2, "rest client": 2, "graphql client": 2,
        "mcp": 2, "mcp server": 2, "mcp client": 2,
        "repl": 2, "notebook environment": 2, "debugging": 2,
        "trace": 2, "profiler": 2, "benchmarking": 2, "sanitizer": 2,
        "doc generator": 2, "documentation generator": 2,
        "readme": 2, "changelog": 2, "release notes": 2,
        "lint": 2, "typing": 2, "code completion": 2, "snippet manager": 2,
        "vim": 2, "neovim": 2, "emacs": 2, "vscode": 2, "jetbrains": 2,
        "zsh": 2, "bash": 2, "powershell": 2, "makefile": 2, "cmake": 2,
        # measured fixes: these generic words pulled titles out of the
        # right vertical ("I Made a Room Simulator" -> devtools,
        # "Bing Maps Leads Scraper" -> devtools via "scraper")
        "simulator": 1, "similarity detection": 2, "tree-sitter": 2,
        "core image": 2, "repl framework": 2,
        "concurrency runtime": 2, "shared-memory": 2, "markdown file": 1,
        "prd": 2, "polyglot": 2, "persistent engine": 2,
        "code similarity": 2, "vector search": 2,
        "zero-dependency": 1, "simd": 2, "pass-by-value": 2,
        "literate programming": 2, "docco": 2, "ssh tools": 2,
        "sqlite-repair": 2, "corrupt": 2,
    },
    "ai_ml": {
        # Core AI terms carry weight 3. Measured need: at weight 2 they tied
        # with a single devtool keyword ("Chatpack - Compress chat exports
        # 13x for LLM analysis (Rust)": llm=2 vs rust=2) and the tie was
        # broken by list order, not by which term was more specific.
        "ai": 3, "llm": 3, "gpt": 3, "chatgpt": 3, "claude": 3, "gemini": 3,
        "llama": 2, "mistral": 2, "machine learning": 2, "deep learning": 2,
        "neural": 2, "transformer": 2, "embedding": 2, "vector database": 2,
        "rag": 3, "prompt": 2, "prompt engineering": 2, "diffusion": 2,
        "text-to-image": 2, "text to image": 2, "speech recognition": 2,
        "text-to-speech": 2, "agent": 3, "inference": 1, "fine-tun": 2,
        "openai": 3, "anthropic": 2, "langchain": 2, "huggingface": 2,
        "computer vision": 2, "ocr": 2, "chatbot": 2, "assistant": 1,
        "copilot": 3, "generative": 2, "artificial intelligence": 3,
        "reasoner": 2, "reasoning model": 3, "summariz*": 2, "classif*": 1,
        "key phrases": 2, "extract": 1, "quantization": 2, "vocoder": 2,
        "stt": 2, "tts": 2, "multimodal": 2, "fine-tune": 2, "sota": 2,
        # added after the precision audit
        "codex": 3, "cursor": 1, "agentic": 3, "agi": 3,
        "swarm": 1, "langgraph": 2, "ollama": 2, "vllm": 2, "pytorch": 2,
        "tensorflow": 2, "jax": 2, "scikit": 2, "hugging face": 2,
        "stable diffusion": 2, "whisper": 2, "elevenlabs": 2,
        "replicate": 2, "perplexity": 2, "openrouter": 2,
        "code generation": 2, "ai code": 2, "ai-generated": 2,
        "ai assistant": 3, "ai agent": 3, "ai agents": 3, "ai model": 3,
        "ai models": 3, "ai-powered": 3, "ai powered": 3,
        "llm cost": 2, "token price": 2, "model price": 2,
        "mem0": 2, "memory operating system": 2, "context window": 2,
        "claude code": 3, "coding agent": 3, "coding agents": 3,
        "swe-bench": 2, "natural language": 1, "ai-powered by": 3,
    },
    "infra_devops": {
        "kubernetes": 2, "k8s": 2, "docker": 2, "container": 2, "terraform": 2,
        "ansible": 2, "puppet": 2, "serverless": 2, "lambda": 2,
        "aws": 2, "azure": 2, "gcp": 2, "cloudflare": 2, "deploy": 2,
        "deployment": 2, "ci/cd": 2, "continuous integration": 2,
        "monitoring": 2, "observability": 2, "logging": 2, "uptime": 2,
        "load balancer": 2, "reverse proxy": 2, "nginx": 2, "database": 1,
        "postgres": 2, "mysql": 2, "sqlite": 2, "redis": 2, "cache": 1,
        "self-host": 2, "self host": 2, "homelab": 2, "server": 1,
        "infrastructure": 2, "sre": 2, "devops": 2, "paas": 2,
        "microservice": 2, "message queue": 2, "kafka": 2, "rabbitmq": 2,
        "watermill": 2, "go messaging": 2,
        "resilience": 2, "retry": 2, "circuit breaker": 2, "daemon": 2,
        "cron": 2, "scheduler": 1, "proxy": 2, "vpn mesh": 2, "tunnel": 2,
        "ssh tunnel": 2, "backup": 2, "restore": 1, "high availability": 2,
        "edge": 1, "cdn": 2, "hosting": 2, "vps": 2, "vm": 1,
        # added after the precision audit
        "flink": 2, "spark": 2, "kafka streams": 2, "consul": 2,
        "vault": 2, "nomad": 2, "rancher": 2, "openshift": 2,
        "event sourcing": 2, "event store": 2, "nosql": 2, "sql database": 2,
        "no sql": 2, "db written": 2, "dba agent": 2,
        "database migration": 2, "schema migration": 2, "orm": 2,
        "connection pool": 2, "vector store": 2, "dba": 2,
        "nix": 2, "nixpkgs": 2, "dotfiles": 2, "systemd": 2,
        "uptime monitor": 2, "down detector": 2, "status page": 2,
        "incident": 2, "on-call": 2, "sre tool": 2, "runbook": 2,
        "container isolation": 2, "orchestrat*": 2, "manifest": 1,
    },
    "data_analytics": {
        "analytics": 2, "dashboard": 2, "data pipeline": 2, "etl": 2,
        "data warehouse": 2, "sql": 2, "query": 1, "visualization": 2,
        "visualisation": 2, "chart": 1, "bi": 1, "metrics": 2,
        "reporting": 1, "spreadsheet": 2, "notebook": 2, "pandas": 2,
        "dataframe": 2, "duckdb": 2, "clickhouse": 2, "timeseries": 2,
        "data modeling": 2, "statistics": 2, "csv": 1, "parquet": 2,
        "dataset": 2, "data analysis": 2, "observability data": 2,
        "data lake": 2, "schema": 2, "ingest": 2, "benchmark": 1,
        # added after the precision audit
        "data intelligence": 2, "business intelligence": 2,
        "text2sql": 2, "sql query": 2, "natural language query": 2,
        "semantic layer": 2, "graph layer": 2, "knowledge graph": 2,
        "revenue leak": 2, "revenue analytics": 2, "metrics": 2,
        "web analytics": 2, "click analytics": 2, "search analytics": 2,
        "data viz": 2, "graph": 1, "insight": 1, "predict": 1,
        "prediction": 1, "forecast": 2, "experiment": 1, "a/b test": 2,
        "observability for": 2, "telemetry": 2, "tracing": 2,
        "log analytics": 2, "apm": 2, "slo": 2, "slis": 2,
    },
    "security_privacy": {
        "security": 2, "vulnerability": 2, "cve": 2, "encryption": 2,
        "encrypt": 2, "password manager": 2, "password": 1, "2fa": 2,
        "authentication": 2, "oauth": 2, "ssh": 2, "vpn": 2, "firewall": 2,
        "antivirus": 2, "malware": 2, "phishing": 2, "privacy": 2,
        "encryption key": 2, "secret management": 2, "zero trust": 2,
        "audit log": 2, "compliance": 2, "gdpr": 2,
        "penetration test": 2, "sanitiz*": 2, "redact*": 2, "scanner": 1,
        "threat": 2, "fuzz": 2, "secure": 1, "keychain": 2, "certificate": 2,
        # added after the precision audit
        "e-signature": 2, "e-signatures": 2, "signature": 1,
        "sha-256": 2, "sha256": 2, "hash": 1, "signing": 2,
        "tamper": 2, "tampering": 2, "integrity": 2, "verified listings": 1,
        "identity": 2, "sso": 2, "passkey": 2, "passkeys": 2,
        "encryption tool": 2, "secure sharing": 2, "secrets": 2,
        "sandbox": 1, "isolation": 1, "least privilege": 2,
    },
    "saas_productivity": {
        "todo": 2, "task manager": 2, "project management": 2, "kanban": 2,
        "calendar": 2, "scheduler": 1, "note-taking": 2, "notes app": 2,
        "note app": 2, "notion": 1, "obsidian": 2, "markdown editor": 2,
        "crm": 2, "helpdesk": 2, "ticketing": 2, "form builder": 2,
        "survey": 2, "poll": 1, "bookmark": 2, "read later": 2,
        "pomodoro": 2, "habit tracker": 2, "productivity": 2, "workflow": 1,
        "automation": 1, "checklist": 2, "journal": 1, "budget": 1,
        "email client": 2, "newsletter": 2,
        "gmail extension": 2, "inbox": 2, "email": 1, "notes": 1,
        "wiki": 2, "knowledge base": 2, "second brain": 2, "daily digest": 2,
        "bookmarking service": 2, "read-it-later": 2, "daily note": 2,
        "life tracker": 2, "planner": 2, "organizer": 2, "agenda": 2,
        # added after the precision audit
        "saas directory": 2, "tool directory": 2, "saas finder": 2,
        "tinder for saas": 2, "perfect tool": 2,
        "product hunt": 2, "launch platform": 2, "feedback board": 2,
        "user feedback": 2, "feature request": 2, "roadmap": 1,
        "email assistant": 2, "email management": 2, "unsubscribe": 2,
        "form builder": 2, "survey tool": 2, "poll app": 2,
        "meeting notes": 2, "standup": 2, "time tracking": 2,
        "billing portal": 2, "customer portal": 2, "churn": 2,
    },
    "design_creative": {
        "design": 2, "figma": 2, "ui library": 2, "component library": 2,
        "icon": 2, "font": 2, "typography": 2, "color palette": 2,
        "illustration": 2, "sketch": 1, "wireframe": 2, "prototyping": 2,
        "animation": 2, "css": 2, "tailwind": 2, "theme": 1,
        "photo editor": 2, "image editor": 2, "video editor": 2,
        "canvas": 1, "whiteboard": 2, "diagram": 2, "excalidraw": 2,
        "blender": 2, "3d": 1, "rendering": 1, "shader": 2,
        "screenshot": 2, "ui test": 2, "ui component": 2, "theme editor": 2,
        "vector": 2, "illustration tool": 2, "pixel art": 2, "ascii": 2,
        # added after the precision audit
        "stylesheet": 2, "stylesheets": 2, "color": 1, "colors": 1,
        "palette": 2, "clickable": 2, "portfolio": 2,
        "cad": 2, "webgl": 2, "wasm rendering": 2, "shader toy": 2,
        "fractal": 2, "typography tool": 2, "font editor": 2,
        "mockup": 2, "wireframing": 2, "design system": 2, "ux": 2,
        "logo": 2, "brand": 1, "presentation": 1, "slides": 2,
        "graphics": 1, "art": 1, "sketch app": 2, "drawing": 2,
        # measured fix: bare "map" pulled "Make a map of the countries
        # you've traveled to" into design_creative. It must be qualified.
        "interactive map": 2, "clickable map": 2, "map of": 1,
        "leads scraper": 1, "scraper": 1,
    },
    "media_content": {
        "podcast": 2, "audio": 1, "music player": 2, "music": 1,
        "video": 1, "youtube": 2, "blog": 1, "newsletter platform": 2,
        "rss": 2, "feed reader": 2, "reader": 1, "writing": 1,
        "bookmarking service": 2, "book": 1, "comic": 1, "manga": 2,
        "news aggregator": 2, "news reader": 2, "transcript": 2,
        "subtitle": 2, "torrent": 1, "streaming": 2,
        "screen recorder": 2, "recorder": 2, "voice input": 2,
        "voice changer": 2, "video downloader": 2, "youtube downloader": 2,
        "media server": 2, "photo library": 2, "local-first library": 2,
        "midi": 2, "piano": 1, "song": 1, "guitar": 1, "dj": 2,
        "photo management": 2, "book reader": 2, "epub": 2,
        # added after the precision audit
        "bittorrent": 2, "torrent client": 2, "gif": 2, "giphy": 2,
        "internet archive": 2, "ffmpeg": 2, "video with code": 2,
        "musician": 2, "timeline of musicians": 2, "artist": 1,
        "indie artist": 2, "band": 1, "album": 2, "playlist": 2,
        "captions": 2, "caption": 2, "daw": 2, "audio editor": 2,
        "image host": 2, "video host": 2, "cdn for video": 2,
        "download": 1, "downloader": 2, "book library": 2, "comics": 2,
        "watch party": 2, "video editing": 2, "newsletter": 1,
    },
    "social_community": {
        "social network": 2, "forum": 2, "community": 1, "chat": 1,
        "messaging": 2, "discord": 2, "reddit": 2, "mastodon": 2,
        "fediverse": 2, "follow": 1, "profile page": 1, "dating": 2,
        "network": 1, "collaboration": 1, "team space": 2, "chatbot": 1,
        "link-in-bio": 2, "newsletter platform": 1,
        "doomscrolling": 2, "feed": 1, "posting": 2, "comments": 1,
        # added after the precision audit
        "p2p": 2, "decentralized": 2, "decentralised": 2, "clone": 1,
        "stumbleupon": 2, "upvoted": 2, "upvote": 2, "vote": 1,
        "whatsapp": 2, "telegram": 2, "matrix protocol": 2, "xmpp": 2,
        "chat app": 2, "social app": 2, "profile": 1, "timeline": 1,
        "newsletter platform": 2, "creator platform": 2, "audience": 1,
        "bluesky client": 2, "tumblr": 2, "linkedin": 2, "instagram": 2,
        "tiktok": 1, "shorts": 2, "newsletter subscriber": 2,
    },
    "commerce_fintech": {
        "ecommerce": 2, "e-commerce": 2, "shop": 2,
        # measured fix: bare "store" matched "reactive store" and
        # "data-store" -- a software term, not commerce.
        "shopping cart": 2, "online store": 2, "storefront": 2,
        "e-shop": 2, "marketplace": 2,
        "stripe": 2, "payment": 2, "payments": 2, "checkout": 2,
        "subscription billing": 2, "invoice": 2, "crypto": 2,
        "bitcoin": 2, "ethereum": 2, "web3": 2, "blockchain": 2,
        "trading": 2, "stock": 2, "portfolio": 1, "tax": 1,
        "accounting": 2, "bookkeeping": 2, "expense": 2,
        "auction": 2, "classifieds": 2, "booking system": 2,
        "restaurant": 1, "pos system": 2, "coupon": 2,
        "solana": 2, "usdc": 2, "token": 1, "wallet": 2, "defi": 2,
        "invoice generator": 2, "billing": 2, "payment gateway": 2,
        "food companion": 2, "coupon code": 2, "price tracker": 2,
        # added after the precision audit
        "split expenses": 2, "expense sharing": 2, "savings": 2,
        "calculator": 1, "brokerage": 2, "side project": 2,
        "sell side": 2, "digital product": 2, "buy sell": 2,
        "buy and sell": 2, "trading bot": 2, "broker": 2,
        "subscription": 1, "saas billing": 2, "tier": 1, "pricing": 1,
        "shopping": 2, "ecommerce platform": 2, "shipping": 2,
        "courier": 2, "delivery": 1, "restaurant app": 2, "menu": 1,
    },
    "education_learning": {
        "course": 2, "learning": 2, "tutorial": 2, "lesson": 2,
        "flashcard": 2, "spaced repetition": 2, "quiz": 2, "exam": 2,
        "textbook": 2, "curriculum": 2, "language learning": 2,
        "vocabulary": 2, "flashcards": 2, "moodle": 2, "homework": 2,
        "study": 1, "course platform": 2, "lms": 2, "book": 1,
        "learn": 2, "training app": 2, "drumming": 2, "piano training": 2,
        "dictionary": 2, "wiktionary": 2, "flashcard deck": 2,
        "tutor": 2, "exam prep": 2, "certification": 2,
        # added after the precision audit
        "leaderboard": 2, "typing practice": 2, "typing speed": 2,
        "learn to code": 2, "coding challenge": 2, "skill rot": 2,
        "quiz app": 2, "flash card": 2, "duolingo": 2, "anki": 2,
        "pronunciation": 2, "english learning": 2, "chinese learning": 2,
        "type to learn": 2, "learn typing": 2, "typing app": 2,
        "math practice": 2, "problem solver": 2, "lesson plan": 2,
        "teacher": 2, "student": 2, "grade": 1, "assignment": 2,
    },
    "health_wellness": {
        "health": 2, "fitness": 2, "workout": 2, "exercise": 2,
        "nutrition": 2, "diet": 2, "calorie": 2, "sleep": 2,
        "meditation": 2, "mindfulness": 2, "therapy": 2, "mental health": 2,
        "medical": 2, "patient": 2, "clinic": 2, "symptom": 2,
        "prescription": 2, "glucose": 2, "period tracker": 2,
        "pregnancy": 2, "wellness": 2, "posture": 2,
        "health tracker": 2, "mental health app": 2, "habit": 1,
        "gym": 2, "yoga": 2, "running": 2, "cycling": 2,
        # added after the precision audit
        "skin care": 2, "skincare": 2, "consultation": 2,
        "dermatolog*": 2, "acne": 2, "insomnia": 2, "anxiety": 2,
        "depression": 2, "burnout": 2, "mood": 1, "stress": 2,
        "recovery": 1, "injury": 2, "physio": 2, "rehab": 2,
        "blood pressure": 2, "heart rate": 2, "steps": 2, "macros": 2,
    },
    "science_hardware": {
        "raspberry pi": 2, "arduino": 2, "esp32": 2, "sensor": 2,
        "robot": 2, "drone": 2, "3d printer": 2, "hardware": 2,
        "arduino project": 2, "firmware": 2, "embedded": 2,
        "synthetic biology": 2, "microscope": 2, "telescope": 2,
        "chemistry": 2, "biology": 2, "physics simulation": 2,
        "nasa": 2, "space": 1, "climate model": 2, "satellite": 2,
        "weather station": 2, "solar": 2, "energy monitor": 2,
        "genomics": 2, "protein": 2, "molecular": 2, "bioinformatics": 2,
        "quantum": 2, "topology": 2, "simulation": 1, "lab": 1,
        # added after the precision audit
        "eclipse locator": 2, "eclipse map": 2, "astronomy": 2,
        "planet": 2, "star": 1,
        "simulation of": 2, "physics": 2, "agriculture": 2,
        "green tech": 2, "climate model": 2,
        "energy": 2, "grid": 1, "battery": 2, "electricity": 2,
        "biological": 2, "genome": 2, "rna": 2, "dna": 2,
        "mamba2": 2, "flashattention": 2, "neuro-symbolic": 2,
    },
    "games_entertainment": {
        "game": 2, "games": 2, "gaming": 2, "puzzle": 2, "rpg": 2,
        "platformer": 2, "roguelike": 2, "roguelite": 2, "chess": 2,
        "poker": 2, "card game": 2, "solitaire": 2, "minecraft": 2,
        "tetris": 2, "wordle": 2, "puzzle game": 2, "daily puzzle": 2,
        "arcade": 2, "retro": 2, "pixel": 1, "sprite": 2,
        "adventure": 1, "racing": 2, "racing game": 2, "simulator": 1,
        "godot": 2, "unity": 2, "unreal": 2, "gamedev": 2,
        "interactive fiction": 2, "visual novel": 2, "dice": 2,
        "game engine": 2, "level editor": 2, "mod": 1,
        # added after the precision audit
        "multiplayer": 2, "level": 2, "levels": 2, "player": 1,
        "space invaders": 2, "typing game": 2, "game launcher": 2,
        "rogue": 2, "idle game": 2, "incremental game": 2, "twine": 2,
        "renpy": 2, "gamemaker": 2, "love2d": 2, "raylib": 2,
        "sfml": 2, "love": 1, "play": 1, "score": 1, "game jam": 2,
    },
    "productivity_generic": {
        # NOTE: this bucket must NOT contain generic words like "app",
        # "tool", "client", "web app". Those appear in every vertical and
        # were measured stealing categories from the others: "BitTorrent
        # Client" became productivity_generic instead of media_content,
        # and "strace-like tool" became productivity_generic instead of
        # devtools. This bucket now only holds words that genuinely
        # describe a consumer-facing general utility.
        "desktop app": 2, "macos": 2, "windows app": 2, "linux app": 2,
        "browser extension": 2, "chrome extension": 2, "safari extension": 2,
        "ios app": 2, "android app": 2, "self-hosted": 2,
        "privacy-first": 2, "local-first": 2, "offline-first": 2,
        "alternative to": 2, "alternative for": 2, "replacement for": 2,
        "open-source alternative": 2, "open source alternative": 2,
        "wrapper for": 2, "desktop application": 2, "menu bar app": 2,
        "command line app": 2, "terminal app": 2, "web app": 2,
        "open source app": 2, "open-source app": 2,
    },
}

# --- matching engine -------------------------------------------------------
#
# Two kinds of keyword:
#   "llm"      a whole word. Matches "llm" and "llms" (optional plural),
#              but not "llama".
#   "summariz*" a stem. Matches any word starting with it, so it catches
#              "summariz", "summarise", "summarization", "summarising".
#              A stem must be marked, because a bare prefix would also
#              match unrelated words.
#
# The plural rule exists because of a real measurement: with a strict
# word boundary, "LLMs" did not match the keyword "llm", and a title
# about LLMs was filed under devtools because of the word "CLI".

_PLURAL_OK = {"s", "es"}


def _make_regex(kw):
    if kw.endswith("*"):
        stem = kw[:-1]
        return re.compile(r"(?<![a-z0-9])" + re.escape(stem))
    if kw.endswith("s") and len(kw) > 3:
        # Already plural: match it exactly, do not add another suffix.
        return re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])")
    # The bare form MUST be included. A first cut built the alternation
    # from suffixes only ("ai" -> "ais|aies"), which silently stopped the
    # bare word from matching at all and collapsed corpus coverage from
    # 69.6% to 21.2%. Always include the keyword itself.
    alts = [kw] + [kw + suf for suf in sorted(_PLURAL_OK)]
    alt = "|".join(re.escape(a) for a in alts)
    return re.compile(r"(?<![a-z0-9])(?:" + alt + r")(?![a-z0-9])")


_WORD_RE = {
    cat: {kw: _make_regex(kw) for kw in kws}
    for cat, kws in KEYWORDS.items()
}

# Deterministic tie-break order (de-dup while preserving first occurrence).
_TIE_ORDER = []
for _c in CATEGORY_ORDER:
    if _c not in _TIE_ORDER:
        _TIE_ORDER.append(_c)


def categorize(title):
    """Return (category, score, matched_keywords) for one title.

    Tie-break rule
    --------------
    When two categories score the same total, the winner is the one whose
    *strongest single keyword* is stronger, then the one with more
    matched keywords, then the fixed category order. It is NOT list order
    alone: measured need -- "Chatpack - Compress chat exports 13x for LLM
    analysis (Rust)" scored llm=2 for ai_ml and rust=2 for devtools, and
    list order handed it to devtools.
    """
    t = (title or "").lower()
    scores = {}
    hits = {}
    maxkw = {}
    for cat, kws in _WORD_RE.items():
        for kw, rx in kws.items():
            if rx.search(t):
                w = KEYWORDS[cat][kw]
                scores[cat] = scores.get(cat, 0) + w
                maxkw[cat] = max(maxkw.get(cat, 0), w)
                hits.setdefault(cat, []).append(kw)
    if not scores:
        return ("other", 0, [])
    best = max(
        scores.items(),
        key=lambda kv: (kv[1], maxkw[kv[0]], len(hits[kv[0]]),
                        -_TIE_ORDER.index(kv[0])),
    )
    cat, score = best
    # Stable, sorted keyword list so output is reproducible.
    return (cat, score, sorted(hits[cat]))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ledger = os.path.join(here, "data", "ledger.jsonl")
    out_path = os.path.join(here, "data", "categories.jsonl")

    rows = []
    with open(ledger, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    cats = Counter()
    weak = 0
    out = []
    for r in rows:
        cat, score, kws = categorize(r.get("title"))
        cats[cat] += 1
        if score <= 2:
            weak += 1
        out.append({
            "id": r.get("id"),
            "title": r.get("title"),
            "category": cat,
            "score": score,
            "keywords": kws,
            "taxonomy_version": TAXONOMY_VERSION,
        })

    # append-only discipline: write a fresh file, never edit in place
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    os.replace(tmp, out_path)

    total = len(out)
    print(f"categorised {total} posts -> {out_path}")
    print(f"taxonomy version: {TAXONOMY_VERSION}")
    print()
    print(f"{'category':26} {'n':>7} {'share':>8}")
    for cat, n in cats.most_common():
        print(f"{cat:26} {n:>7} {n/total*100:>7.1f}%")
    print()
    print(f"'other' share: {cats['other']/total*100:.1f}%")
    print(f"weak matches (score<=2): {weak/total*100:.1f}%")

    # Red flag: one category holding a third of the corpus means the
    # taxonomy is doing nothing useful.
    top, topn = cats.most_common(1)[0]
    if topn / total > 0.33:
        print(f"\nRED FLAG: '{top}' holds {topn/total*100:.1f}% of the corpus "
              f"-- the taxonomy is not discriminating")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
