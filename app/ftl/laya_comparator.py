"""Laya Model Content-Based EML Duplicate Detection and Semantic Comparison.

Provides:
1. Deterministic EML content extraction, cleaning, and normalization.
2. Content fingerprinting (SHA-256) for exact duplicate detection regardless of filename.
3. Laya LLM-powered semantic content comparison for detecting slight modifications,
   wording variations, or possible duplicates requiring review.
4. Configurable similarity thresholds and robust fallbacks for malformed inputs or model failures.
"""
from __future__ import annotations

import email
import hashlib
import json
import logging
import re
import threading
import unicodedata
from dataclasses import asdict, dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("orchestrator.ftl.laya_comparator")

# Configurable Similarity Thresholds
EXACT_MATCH_THRESHOLD = 1.0
DEFAULT_SEMANTIC_DUPLICATE_THRESHOLD = 0.90
DEFAULT_REVIEW_THRESHOLD = 0.75


@dataclass
class DuplicateCheckResult:
    """Result of comparing an EML against existing records or comparing two EMLs."""
    is_duplicate: bool
    is_potential_duplicate: bool
    match_type: str  # "exact_hash", "semantic_duplicate", "possible_duplicate_for_review", "distinct"
    similarity_score: float
    reason: str
    matched_run_id: Optional[Any] = None
    matched_filename: Optional[str] = None
    confidence: float = 1.0
    differences: List[str] = field(default_factory=list)
    fingerprint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_whitespace(text: str) -> str:
    """Normalize unicode and collapse repeated spaces and newlines."""
    if not text:
        return ""
    # Unicode NFKC normalization
    text = unicodedata.normalize("NFKC", str(text))
    # Replace carriage returns
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Replace multiple horizontal spaces/tabs with single space
    text = re.sub(r"[ \t]+", " ", text)
    # Collapse 3+ newlines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_email_address(raw_addr: str) -> str:
    """Extract clean lowercase email address without display names or angle brackets."""
    if not raw_addr:
        return ""
    name, addr = parseaddr(str(raw_addr).strip())
    clean = (addr or name or raw_addr).strip().lower()
    clean = re.sub(r"^<|>$", "", clean).strip()
    return clean


def normalize_address_list(raw_headers: str | List[str]) -> List[str]:
    """Parse comma/semicolon separated email addresses into a sorted list of unique clean addresses."""
    if not raw_headers:
        return []
    if isinstance(raw_headers, list):
        items = raw_headers
    else:
        items = re.split(r"[,;]", str(raw_headers))
    cleaned = set()
    for item in items:
        addr = normalize_email_address(item)
        if addr and "@" in addr:
            cleaned.add(addr)
        elif addr:
            cleaned.add(addr)
    return sorted(cleaned)


def normalize_subject(subject: str) -> str:
    """Normalize subject line by stripping Re/Fwd prefixes, external tags, and extra spaces."""
    if not subject:
        return ""
    s = unicodedata.normalize("NFKC", str(subject)).strip().lower()
    # Strip common email prefixes iteratively
    prefix_re = re.compile(r"^(?:(?:re|fwd|fw|automatic reply|out of office|external|ext)[\s:;\-\]]+)+", re.IGNORECASE)
    while True:
        cleaned = prefix_re.sub("", s).strip()
        cleaned = re.sub(r"^\[(?:external|caution|spam)\]\s*", "", cleaned, flags=re.IGNORECASE).strip()
        if cleaned == s:
            break
        s = cleaned
    return re.sub(r"\s+", " ", s).strip()


import html


def clean_email_body(body_text: str) -> str:
    """Strip HTML artifacts, email reply quotes, and signatures for canonical content comparison."""
    if not body_text:
        return ""
    text = str(body_text)
    # Unescape HTML entities
    text = html.unescape(text)
    # If body contains HTML tags, strip scripts and tags
    if any(tag in text.lower() for tag in ("<html", "<body", "<div", "<p", "<br", "<span", "<table")):
        text = re.sub(r"(?is)<(script|style|head).*?>.*?</\1>", " ", text)
        text = re.sub(r"<br\s*/?>", " \n ", text, flags=re.IGNORECASE)
        text = re.sub(r"</p>", " \n ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)

    lines = text.split("\n")
    cleaned_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        # Skip standard quoted email reply lines
        if stripped.startswith(">") or stripped.startswith("|"):
            continue
        if re.match(r"^on\s+.+wrote:\s*$", stripped, flags=re.IGNORECASE):
            continue
        if re.match(r"^-+\s*original message\s*-+", stripped, flags=re.IGNORECASE):
            break
        if re.match(r"^from:\s*.+sent:\s*.+to:\s*.+subject:\s*.+", stripped, flags=re.IGNORECASE):
            break
        if stripped:
            cleaned_lines.append(stripped)

    joined = " ".join(cleaned_lines)
    # Normalize all internal whitespace and lowercase for canonical content matching
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", joined)).strip().lower()


def parse_and_extract_eml(raw_bytes: bytes, filename: str = "") -> Dict[str, Any]:
    """Parse raw .eml bytes into structured email metadata, body, and attachment hashes."""
    if not raw_bytes:
        return {
            "from": "",
            "to": [],
            "cc": [],
            "subject": "",
            "raw_subject": "",
            "date": "",
            "message_id": "",
            "body_text": "",
            "normalized_body": "",
            "attachments": [],
            "content_fingerprint": "",
            "filename": filename,
        }

    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    except Exception as exc:
        logger.warning(f"Failed standard EML parse, attempting fallback: {exc}")
        try:
            msg = email.message_from_bytes(raw_bytes)
        except Exception:
            # Degraded fallback for malformed bytes
            text_preview = raw_bytes[:10000].decode("utf-8", errors="replace")
            clean_text = clean_email_body(text_preview)
            fp = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
            return {
                "from": "",
                "to": [],
                "cc": [],
                "subject": "",
                "raw_subject": "",
                "date": "",
                "message_id": "",
                "body_text": text_preview,
                "normalized_body": clean_text,
                "attachments": [],
                "content_fingerprint": fp,
                "filename": filename,
            }

    raw_subject = str(msg.get("Subject", "") or "")
    raw_from = str(msg.get("From", "") or "")
    raw_to = str(msg.get("To", "") or "")
    raw_cc = str(msg.get("Cc", "") or "")
    date_hdr = str(msg.get("Date", "") or "")
    msg_id = str(msg.get("Message-ID", "") or "").strip()

    body_plain: Optional[str] = None
    body_html: Optional[str] = None
    attachments: List[Dict[str, Any]] = []

    def walk_parts(m: Any) -> None:
        nonlocal body_plain, body_html
        if m.is_multipart():
            for part in m.iter_parts():
                walk_parts(part)
            return

        disp = m.get_content_disposition()
        fname = m.get_filename()
        ct = m.get_content_type()

        # Handle attachment parts
        if disp == "attachment" or (fname and disp != "inline"):
            try:
                content = m.get_content()
            except Exception:
                content = None
            if isinstance(content, str):
                content_bytes = content.encode("utf-8", "replace")
            elif isinstance(content, (bytes, bytearray)):
                content_bytes = bytes(content)
            else:
                content_bytes = b""
            
            att_hash = hashlib.sha256(content_bytes).hexdigest() if content_bytes else ""
            attachments.append({
                "filename": fname or "attachment",
                "content_type": ct,
                "size_bytes": len(content_bytes),
                "sha256": att_hash,
                "bytes": content_bytes,
            })
        elif ct == "text/plain" and body_plain is None and disp != "attachment":
            try:
                body_plain = m.get_content()
            except Exception:
                pass
        elif ct == "text/html" and body_html is None and disp != "attachment":
            try:
                body_html = m.get_content()
            except Exception:
                pass

    try:
        walk_parts(msg)
    except Exception as exc:
        logger.warning(f"Error traversing MIME parts: {exc}")

    raw_body = body_plain or body_html or ""
    clean_body = clean_email_body(raw_body)
    norm_subj = normalize_subject(raw_subject)
    norm_from = normalize_email_address(raw_from)
    norm_to = normalize_address_list(raw_to)
    norm_cc = normalize_address_list(raw_cc)

    # Attachments canonical summary (sorted by filename & sha256)
    att_meta = []
    for att in attachments:
        att_meta.append({
            "filename": (att.get("filename") or "").lower(),
            "content_type": att.get("content_type", ""),
            "size_bytes": att.get("size_bytes", 0),
            "sha256": att.get("sha256", ""),
        })
    att_meta_sorted = sorted(att_meta, key=lambda x: (x["filename"], x["sha256"]))

    # Generate deterministic canonical fingerprint
    fingerprint = compute_content_fingerprint(
        sender=norm_from,
        recipients=norm_to,
        subject=norm_subj,
        cleaned_body=clean_body,
        attachment_hashes=[a["sha256"] for a in att_meta_sorted if a.get("sha256")],
    )

    return {
        "from": norm_from,
        "raw_from": raw_from,
        "to": norm_to,
        "raw_to": raw_to,
        "cc": norm_cc,
        "subject": norm_subj,
        "raw_subject": raw_subject,
        "date": date_hdr,
        "message_id": msg_id,
        "body_text": raw_body,
        "normalized_body": clean_body,
        "attachments": attachments,
        "attachments_meta": att_meta_sorted,
        "content_fingerprint": fingerprint,
        "filename": filename,
    }


def compute_content_fingerprint(
    sender: str,
    recipients: List[str],
    subject: str,
    cleaned_body: str,
    attachment_hashes: Optional[List[str]] = None,
) -> str:
    """Generate a deterministic SHA-256 content fingerprint from normalized email fields."""
    recips_str = ",".join(sorted(str(r).strip().lower() for r in (recipients or []) if str(r).strip()))
    att_str = ",".join(sorted(str(a).strip().lower() for a in (attachment_hashes or []) if str(a).strip()))
    canonical_repr = (
        f"FROM:{str(sender).strip().lower()}\n"
        f"TO:{recips_str}\n"
        f"SUBJECT:{str(subject).strip().lower()}\n"
        f"BODY:{str(cleaned_body).strip().lower()}\n"
        f"ATTACHMENTS:{att_str}"
    )
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


def compute_lexical_similarity(text_a: str, text_b: str) -> float:
    """Compute fast lexical token overlap (Jaccard + length penalty) between two texts."""
    if not text_a and not text_b:
        return 1.0
    if not text_a or not text_b:
        return 0.0

    tokens_a = set(re.findall(r"\w+", text_a.lower()))
    tokens_b = set(re.findall(r"\w+", text_b.lower()))

    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0

    intersection = tokens_a.intersection(tokens_b)
    union = tokens_a.union(tokens_b)
    jaccard = len(intersection) / len(union)

    # Character-level edit similarity for short texts
    len_diff = abs(len(text_a) - len(text_b))
    max_len = max(len(text_a), len(text_b))
    len_sim = 1.0 - (len_diff / max_len) if max_len > 0 else 1.0

    return 0.7 * jaccard + 0.3 * len_sim


_laya_agent: Any = None
_laya_agent_init_failed = False
_laya_agent_lock = threading.Lock()


def get_laya_agent() -> Any:
    """Lazily load the singleton native Laya multilingual decision agent."""
    global _laya_agent, _laya_agent_init_failed
    if _laya_agent is not None:
        return _laya_agent
    if _laya_agent_init_failed:
        return None

    with _laya_agent_lock:
        if _laya_agent is not None:
            return _laya_agent
        if _laya_agent_init_failed:
            return None
        try:
            import laya
            logger.info("Initializing native Laya multilingual System 1 decision agent...")
            _laya_agent = laya.load("ml")
            logger.info("Native Laya agent loaded successfully.")
            return _laya_agent
        except Exception as exc:
            logger.warning(f"Could not load native Laya agent ({exc}); will use fallback comparator.", exc_info=True)
            _laya_agent_init_failed = True
            return None


class LayaContentComparator:
    """Laya Model semantic comparator for EML content duplicate detection.

    Combines:
    1. Deterministic hashing for exact duplicates.
    2. Fast heuristic filtering.
    3. Native Laya System 1 decision model for calibrated, non-autoregressive duplicate classification (~33ms).
    4. Robust LLM / lexical fallback if the native model is unavailable.
    """

    def __init__(
        self,
        semantic_threshold: float = DEFAULT_SEMANTIC_DUPLICATE_THRESHOLD,
        review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
    ):
        self.semantic_threshold = semantic_threshold
        self.review_threshold = review_threshold

    def compare_exact(self, email_a: Dict[str, Any], email_b: Dict[str, Any]) -> Optional[DuplicateCheckResult]:
        """Check if two emails have identical normalized content fingerprints."""
        fp_a = email_a.get("content_fingerprint")
        fp_b = email_b.get("content_fingerprint")

        if fp_a and fp_b and fp_a == fp_b:
            return DuplicateCheckResult(
                is_duplicate=True,
                is_potential_duplicate=True,
                match_type="exact_hash",
                similarity_score=1.0,
                reason="Identical normalized email content fingerprint match.",
                confidence=1.0,
                fingerprint=fp_a,
            )
        return None

    def _compare_with_native_laya(
        self,
        new_email: Dict[str, Any],
        candidate_email: Dict[str, Any],
        fallback_sim: float,
    ) -> Optional[DuplicateCheckResult]:
        """Evaluate two emails using native Laya non-autoregressive decision model."""
        agent = get_laya_agent()
        if agent is None:
            return None

        state_text = self._build_laya_comparison_prompt(new_email, candidate_email)
        questions = {
            "relationship": {
                "type": "choice",
                "instructions": "Determine whether RFQ Email 2 is an exact duplicate, a minor revision/resubmission, or a distinct inquiry compared to RFQ Email 1.",
                "criteria": {
                    "distinct": "Completely different inquiry, different customer project, or different equipment items requested",
                    "possible_duplicate": "Minor wording revision, resubmission, updated deadline, or follow-up for the same project/equipment",
                    "exact_duplicate": "Identical RFQ inquiry and scope",
                },
            }
        }

        try:
            res = agent.predict(state=state_text, questions=questions)
            answers = res.get("answers", {})
            rel_ans = answers.get("relationship", {})
            choice = rel_ans.get("choice")
            probs = rel_ans.get("probabilities", {})
            confidence = float(rel_ans.get("answer_confidence") or rel_ans.get("confidence") or 0.90)

            p_exact = float(probs.get("exact_duplicate", 0.0))
            p_possible = float(probs.get("possible_duplicate", 0.0))
            p_distinct = float(probs.get("distinct", 0.0))

            if choice == "exact_duplicate" and p_exact >= 0.50:
                sim_score = round(max(0.90, p_exact), 3)
                return DuplicateCheckResult(
                    is_duplicate=True,
                    is_potential_duplicate=True,
                    match_type="semantic_duplicate",
                    similarity_score=sim_score,
                    reason=f"Native Laya model identified identical RFQ inquiry (probability: {int(p_exact*100)}%).",
                    confidence=round(confidence, 3),
                    fingerprint=new_email.get("content_fingerprint"),
                )
            elif choice == "possible_duplicate" and p_possible >= 0.45:
                sim_score = round(max(0.75, min(0.89, 0.75 + p_possible * 0.14)), 3)
                return DuplicateCheckResult(
                    is_duplicate=False,
                    is_potential_duplicate=True,
                    match_type="possible_duplicate_for_review",
                    similarity_score=sim_score,
                    reason=f"Native Laya model identified a probable revision or modified RFQ (probability: {int(p_possible*100)}%).",
                    confidence=round(confidence, 3),
                    fingerprint=new_email.get("content_fingerprint"),
                )
            else:
                sim_score = round(max(0.0, 1.0 - p_distinct), 3)
                return DuplicateCheckResult(
                    is_duplicate=False,
                    is_potential_duplicate=False,
                    match_type="distinct",
                    similarity_score=sim_score,
                    reason=f"Native Laya model classified as distinct inquiry (probability: {int(p_distinct*100)}%).",
                    confidence=round(confidence, 3),
                    fingerprint=new_email.get("content_fingerprint"),
                )
        except Exception as exc:
            logger.warning(f"Native Laya prediction failed ({exc}), falling back to alternative analyzer.")
            return None

    def compare_emails_sync(
        self,
        new_email: Dict[str, Any],
        candidate_email: Dict[str, Any],
        llm_overrides: Optional[Dict[str, Any]] = None,
    ) -> DuplicateCheckResult:
        """Synchronously compare two emails using exact hash, lexical filter, and native Laya model."""
        # 1. Exact hash check
        exact_res = self.compare_exact(new_email, candidate_email)
        if exact_res:
            return exact_res

        # 2. Heuristic text & subject similarity
        subj_sim = compute_lexical_similarity(
            new_email.get("subject", ""),
            candidate_email.get("subject", ""),
        )
        body_sim = compute_lexical_similarity(
            new_email.get("normalized_body", ""),
            candidate_email.get("normalized_body", ""),
        )
        combined_heuristic = 0.4 * subj_sim + 0.6 * body_sim

        # If completely different (heuristic < 0.40 and senders/subjects distinct)
        same_sender = (
            new_email.get("from") and candidate_email.get("from")
            and new_email.get("from") == candidate_email.get("from")
        )
        if combined_heuristic < 0.40 and not same_sender and subj_sim < 0.45:
            return DuplicateCheckResult(
                is_duplicate=False,
                is_potential_duplicate=False,
                match_type="distinct",
                similarity_score=round(combined_heuristic, 3),
                reason="Completely different email content, subject, and sender.",
                confidence=0.95,
                fingerprint=new_email.get("content_fingerprint"),
            )

        # 3. Native Laya System 1 Decision Model (when not explicitly overridden by LLM overrides)
        if not llm_overrides:
            native_res = self._compare_with_native_laya(new_email, candidate_email, combined_heuristic)
            if native_res is not None:
                return native_res

        # 4. Fallback: LLM Semantic Comparison
        try:
            from app.ftl.llm import open_client, resolve_fallback_llm_config, resolve_llm_config

            config = resolve_llm_config(llm_overrides)
            client, model_name = open_client(config, timeout=25.0, max_retries=1)

            prompt = self._build_laya_comparison_prompt(new_email, candidate_email)
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are the Laya RFQ duplicate detection and semantic comparison model. "
                            "Analyze two elevator-parts RFQ emails and determine if they represent the exact same inquiry, "
                            "a slightly modified revision/resubmission, or completely different RFQ requests. "
                            "Respond ONLY in valid JSON format."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=600,
            )

            raw_reply = response.choices[0].message.content or ""
            return self._parse_laya_response(raw_reply, new_email.get("content_fingerprint"), combined_heuristic)

        except Exception as exc:
            logger.warning(f"Laya LLM call failed or unavailable ({exc}), falling back to lexical analysis: {exc}")
            # Graceful fallback: classify based on heuristic
            if combined_heuristic >= self.semantic_threshold:
                return DuplicateCheckResult(
                    is_duplicate=True,
                    is_potential_duplicate=True,
                    match_type="semantic_duplicate",
                    similarity_score=round(combined_heuristic, 3),
                    reason=f"High textual similarity ({int(combined_heuristic*100)}%) detected via fallback analyzer.",
                    confidence=0.75,
                    fingerprint=new_email.get("content_fingerprint"),
                )
            elif combined_heuristic >= self.review_threshold:
                return DuplicateCheckResult(
                    is_duplicate=False,
                    is_potential_duplicate=True,
                    match_type="possible_duplicate_for_review",
                    similarity_score=round(combined_heuristic, 3),
                    reason=f"Moderate textual similarity ({int(combined_heuristic*100)}%) detected. Flagged for review.",
                    confidence=0.70,
                    fingerprint=new_email.get("content_fingerprint"),
                )
            else:
                return DuplicateCheckResult(
                    is_duplicate=False,
                    is_potential_duplicate=False,
                    match_type="distinct",
                    similarity_score=round(combined_heuristic, 3),
                    reason="Low similarity detected between emails. Treated as a new email.",
                    confidence=0.85,
                    fingerprint=new_email.get("content_fingerprint"),
                )

    def find_duplicate_in_runs(
        self,
        new_email: Dict[str, Any],
        runs: List[Dict[str, Any]],
        llm_overrides: Optional[Dict[str, Any]] = None,
    ) -> DuplicateCheckResult:
        """Scan past runs for exact hash duplicates first, then evaluate semantic similarity."""
        new_fp = new_email.get("content_fingerprint")

        # 1. Exact hash search across all runs
        if new_fp:
            for run in runs:
                run_fp = run.get("content_fingerprint")
                if run_fp and run_fp == new_fp:
                    return DuplicateCheckResult(
                        is_duplicate=True,
                        is_potential_duplicate=True,
                        match_type="exact_hash",
                        similarity_score=1.0,
                        reason=f"Exact normalized content match with previous Run #{run.get('id')}.",
                        matched_run_id=run.get("id"),
                        matched_filename=run.get("input_filename"),
                        confidence=1.0,
                        fingerprint=new_fp,
                    )

        # 2. Semantic comparison against candidate runs
        # Filter candidate runs that have email metadata or candidate text
        candidates: List[Dict[str, Any]] = []
        for run in runs:
            # Skip runs that failed or have no content
            cand_body = run.get("normalized_content") or run.get("candidate_text") or ""
            if not cand_body:
                continue
            candidates.append(run)

        best_result: Optional[DuplicateCheckResult] = None
        for run in candidates[:15]:  # Limit deep checks to most recent 15 relevant candidates
            cand_email_repr = {
                "from": (run.get("email_metadata") or {}).get("from", ""),
                "subject": (run.get("email_metadata") or {}).get("subject", ""),
                "normalized_body": run.get("normalized_content") or run.get("candidate_text", "")[:4000],
                "content_fingerprint": run.get("content_fingerprint"),
            }
            res = self.compare_emails_sync(new_email, cand_email_repr, llm_overrides=llm_overrides)
            if res.is_duplicate or res.is_potential_duplicate:
                res.matched_run_id = run.get("id")
                res.matched_filename = run.get("input_filename")
                if res.is_duplicate:
                    return res  # Immediate match
                if best_result is None or res.similarity_score > best_result.similarity_score:
                    best_result = res

        if best_result:
            return best_result

        return DuplicateCheckResult(
            is_duplicate=False,
            is_potential_duplicate=False,
            match_type="distinct",
            similarity_score=0.0,
            reason="No duplicate or similar previous RFQ email found in database. Treated as a new email.",
            confidence=1.0,
            fingerprint=new_fp,
        )

    def _build_laya_comparison_prompt(self, email_a: Dict[str, Any], email_b: Dict[str, Any]) -> str:
        body_a = (email_a.get("normalized_body") or email_a.get("body_text") or "")[:2500]
        body_b = (email_b.get("normalized_body") or email_b.get("body_text") or "")[:2500]

        att_a = [a.get("filename") for a in email_a.get("attachments_meta", []) if isinstance(a, dict)]
        att_b = [a.get("filename") for a in email_b.get("attachments_meta", []) if isinstance(a, dict)]

        return f"""Compare the following two RFQ emails:

--- EMAIL 1 (Previous Upload) ---
From: {email_b.get('from', 'Unknown')}
Subject: {email_b.get('subject', 'No Subject')}
Attachments: {', '.join(att_b) if att_b else 'None'}
Body:
{body_b}

--- EMAIL 2 (New Upload) ---
From: {email_a.get('from', 'Unknown')}
Subject: {email_a.get('subject', 'No Subject')}
Attachments: {', '.join(att_a) if att_a else 'None'}
Body:
{body_a}

Instructions:
1. Determine if Email 2 is an exact duplicate, a slightly modified version/revision, or a completely different inquiry.
2. Minor differences include: slight wording changes, updated deadlines, greeting variations, reordered sentences, or minor formatting changes for the same project/equipment.
3. Compute a similarity score between 0.00 and 1.00.
   - >= 0.90: Semantic duplicate (treat as duplicate)
   - 0.75 - 0.89: Possible duplicate requiring review
   - < 0.75: Distinct new email
4. Output your analysis in the following strict JSON schema:
{{
  "similarity_score": <float between 0.0 and 1.0>,
  "is_duplicate": <true if similarity >= 0.90 else false>,
  "is_potential_duplicate": <true if similarity >= 0.75 else false>,
  "match_type": "<'semantic_duplicate' | 'possible_duplicate_for_review' | 'distinct'>",
  "reason": "<1-2 sentence concise explanation of why they match or differ>",
  "differences": ["<list of key differences, if any>"],
  "confidence": <float between 0.0 and 1.0>
}}
"""

    def _parse_laya_response(
        self,
        raw_text: str,
        fingerprint: Optional[str],
        fallback_sim: float,
    ) -> DuplicateCheckResult:
        clean = raw_text.strip()
        if clean.startswith("```json"):
            clean = clean[7:]
        if clean.startswith("```"):
            clean = clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()

        try:
            data = json.loads(clean)
            score = float(data.get("similarity_score", fallback_sim))
            # Bound score between 0.0 and 1.0
            score = max(0.0, min(1.0, score))

            if score >= self.semantic_threshold:
                match_type = "semantic_duplicate"
                is_dup = True
                is_pot = True
            elif score >= self.review_threshold:
                match_type = "possible_duplicate_for_review"
                is_dup = False
                is_pot = True
            else:
                match_type = "distinct"
                is_dup = False
                is_pot = False

            return DuplicateCheckResult(
                is_duplicate=is_dup,
                is_potential_duplicate=is_pot,
                match_type=data.get("match_type") or match_type,
                similarity_score=round(score, 3),
                reason=str(data.get("reason") or "Laya model evaluated semantic similarity."),
                confidence=float(data.get("confidence", 0.9)),
                differences=list(data.get("differences", [])),
                fingerprint=fingerprint,
            )
        except Exception as exc:
            logger.warning(f"Failed to parse Laya LLM response JSON ({exc}): {raw_text[:200]}")
            return DuplicateCheckResult(
                is_duplicate=fallback_sim >= self.semantic_threshold,
                is_potential_duplicate=fallback_sim >= self.review_threshold,
                match_type="possible_duplicate_for_review" if fallback_sim >= self.review_threshold else "distinct",
                similarity_score=round(fallback_sim, 3),
                reason="Evaluated via fallback comparison heuristics.",
                confidence=0.75,
                fingerprint=fingerprint,
            )
