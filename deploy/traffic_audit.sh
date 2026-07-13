#!/usr/bin/env bash
# Read-only traffic audit for the nginx access logs on the diytracker box.
# Answers "is this traffic growth legitimate?" — reports findings only, never
# changes anything. Run on the server:
#   sudo bash deploy/traffic_audit.sh            # root/adm needed to read /var/log/nginx
#   DAYS=7 sudo -E bash deploy/traffic_audit.sh  # only the last week (default: 30 days)
#   DAYS=0 sudo -E bash deploy/traffic_audit.sh  # no time limit — all log data
#   LOG_GLOB='/var/log/nginx/access.log*' bash deploy/traffic_audit.sh
#
# Notes on what the log contains: static files and the favicon have
# access_log off in the nginx config, so every line here is a real request
# that reached (or was routed toward) the app — no asset noise.
set -u

LOG_GLOB="${LOG_GLOB:-/var/log/nginx/access.log*}"
TOP_N="${TOP_N:-15}"
TREND_DAYS="${TREND_DAYS:-14}"
DAYS="${DAYS:-30}"   # analyze only the last N days of log data; DAYS=0 = everything

PASS=0 WARN=0
ok()   { printf '  \033[32m[ OK ]\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
warn() { printf '  \033[33m[WARN]\033[0m %s\n' "$1"; WARN=$((WARN+1)); }
info() { printf '  \033[36m[INFO]\033[0m %s\n' "$1"; }
section() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

# shellcheck disable=SC2206
LOGS=($LOG_GLOB)
if [ ! -e "${LOGS[0]}" ]; then
  printf 'No logs match %s (need root or membership in the adm group?)\n' "$LOG_GLOB" >&2
  exit 1
fi

# Cutoff for the DAYS window as yyyymmdd (GNU date on the server; BSD
# fallback so the script also runs on a Mac against copied logs).
CUTOFF=""
if [ "$DAYS" -gt 0 ]; then
  CUTOFF=$(date -d "$DAYS days ago" +%Y%m%d 2>/dev/null || date -v -"${DAYS}d" +%Y%m%d)
fi

# Emit all matched logs (rotated + gzipped included), filtered to the DAYS
# window. Oldest data first not guaranteed — every analysis below is
# order-independent.
dump() {
  zcat -f -- "${LOGS[@]}" 2>/dev/null | awk -v cutoff="$CUTOFF" '
    cutoff == "" { print; next }
    {
      if (!match($0, /\[[0-9]{1,2}\/[A-Za-z]{3}\/[0-9]{4}/)) next;
      split(substr($0, RSTART+1, RLENGTH-1), dt, "/");
      m = (index("JanFebMarAprMayJunJulAugSepOctNovDec", dt[2]) + 2) / 3;
      if (sprintf("%04d%02d%02d", dt[3], m, dt[1]) >= cutoff) print
    }'
}

# Combined log format, parsed by splitting on '"':
#   pre='ip - user [time] '  $2=request  post=' status bytes '  $4=referer  $6=user-agent
# awk helper prefix shared by most passes:
AWK_COMBINED='
function ip()      { split($1, a, " "); return a[1] }
function day()     { if (!match($1, /\[[0-9]{1,2}\/[A-Za-z]{3}\/[0-9]{4}/)) return ""; return substr($1, RSTART+1, RLENGTH-1) }
function minute()  { if (!match($1, /\[[^ ]+/)) return ""; t=substr($1, RSTART+1, RLENGTH-1); sub(/:[0-9]{2}$/, "", t); return t }  # dd/Mon/yyyy:hh:mm
function req()     { return $2 }
function path()    { split($2, r, " "); p=r[2]; sub(/\?.*/, "", p); return p }
function method()  { split($2, r, " "); return r[1] }
function status()  { split($3, s, " "); return s[1] }
function referer() { return $4 }
function ua()      { return $6 }
'

TOTAL=$(dump | wc -l | tr -d ' ')
UNIQ_IPS=$(dump | awk -F'"' "$AWK_COMBINED"'{print ip()}' | sort -u | wc -l | tr -d ' ')

section "Log coverage"
info "Files: ${LOGS[*]}"
if [ -n "$CUTOFF" ]; then
  info "Window: last $DAYS days (since $CUTOFF; older lines ignored — DAYS=0 for everything)"
else
  info "Window: all available log data (set DAYS=N to limit)"
fi
info "Total requests: $TOTAL from $UNIQ_IPS unique IPs"
[ "$TOTAL" -eq 0 ] && { warn "Logs are empty — nothing to analyze"; exit 0; }

# ── 1. Daily trend: is the growth real page views or one hammering source? ──
section "Daily trend (last $TREND_DAYS days: requests | unique IPs | share of top IP)"
dump | awk -F'"' "$AWK_COMBINED"'
  { d=day(); if (d=="") next; i=ip(); reqs[d]++; if (!(d SUBSEP i in seen)) { seen[d,i]=1; uniq[d]++ }
    peripd[d,i]++; if (peripd[d,i] > topc[d]) { topc[d]=peripd[d,i] } }
  END { for (d in reqs) printf "%s %d %d %.0f\n", d, reqs[d], uniq[d], 100*topc[d]/reqs[d] }
' | sort -t/ -k3,3n -k2,2M -k1,1n | tail -n "$TREND_DAYS" \
  | awk '{ printf "  %s  %7d req  %6d IPs  top IP: %s%%\n", $1, $2, $3, $4 }'
info "Legit growth: requests and unique IPs rise together, top-IP share stays low."
info "Suspicious: requests explode while unique IPs stay flat, or one IP owns a big share."

# ── 2. Traffic concentration ─────────────────────────────────────────────────
section "Top $TOP_N IPs (all logs)"
dump | awk -F'"' "$AWK_COMBINED"'{print ip()}' | sort | uniq -c | sort -rn | head -n "$TOP_N" \
  | awk -v t="$TOTAL" '{ printf "  %8d (%5.1f%%)  %s\n", $1, 100*$1/t, $2 }'
TOP10_SHARE=$(dump | awk -F'"' "$AWK_COMBINED"'{print ip()}' | sort | uniq -c | sort -rn | head -10 \
  | awk -v t="$TOTAL" '{ s+=$1 } END { printf "%.0f", 100*s/t }')
if [ "${TOP10_SHARE:-0}" -ge 50 ]; then
  warn "Top 10 IPs account for ${TOP10_SHARE}% of all traffic — growth is a few heavy clients, not an audience"
else
  ok "Top 10 IPs account for ${TOP10_SHARE}% of traffic — reasonably distributed"
fi

# ── 3. Burst rate: worst requests-per-minute by a single IP ─────────────────
section "Burst detection (highest requests/minute from one IP)"
dump | awk -F'"' "$AWK_COMBINED"'{ m=minute(); if (m=="") next; print ip(), m }' | sort | uniq -c | sort -rn | head -5 \
  | awk '{ printf "  %6d req/min  %-16s at %s\n", $1, $2, $3 }'
MAXRPM=$(dump | awk -F'"' "$AWK_COMBINED"'{ m=minute(); if (m=="") next; print ip(), m }' | sort | uniq -c | sort -rn | head -1 | awk '{print $1}')
if [ "${MAXRPM:-0}" -ge 120 ]; then
  warn "Peak of $MAXRPM req/min from a single IP — humans don't do that; consider nginx limit_req (none configured today)"
else
  ok "Peak single-IP rate is ${MAXRPM:-0} req/min — within human/polite-bot range"
fi

# ── 4. User agents ───────────────────────────────────────────────────────────
section "Top $TOP_N user agents"
dump | awk -F'"' "$AWK_COMBINED"'{print ua()}' | sort | uniq -c | sort -rn | head -n "$TOP_N" \
  | awk -v t="$TOTAL" '{ c=$1; $1=""; printf "  %8d (%5.1f%%) %s\n", c, 100*c/t, substr($0,2,110) }'

SCRIPTED=$(dump | awk -F'"' "$AWK_COMBINED"'{u=tolower(ua());
  if (u=="-" || u=="" || u ~ /curl|wget|python|go-http|scrapy|libwww|okhttp|aiohttp|httpclient|java\//) n++ }
  END { print n+0 }')
SCRIPTED_PCT=$(( SCRIPTED * 100 / TOTAL ))
if [ "$SCRIPTED_PCT" -ge 20 ]; then
  warn "Scripted/empty user agents (curl, python, wget, blank, …): $SCRIPTED requests (${SCRIPTED_PCT}%)"
else
  ok "Scripted/empty user agents: $SCRIPTED requests (${SCRIPTED_PCT}%)"
fi

# One IP cycling through many UAs is a classic scraper-evading-blocks pattern.
section "IPs rotating user agents (>5 distinct UAs from one IP)"
ROTATORS=$(dump | awk -F'"' "$AWK_COMBINED"'{print ip() "\t" ua()}' | sort -u \
  | cut -f1 | uniq -c | awk '$1 > 5 { printf "  %3d UAs  %s\n", $1, $2 }')
if [ -n "$ROTATORS" ]; then
  warn "IPs presenting many different user agents:"
  printf '%s\n' "$ROTATORS" | head -n "$TOP_N"
else
  ok "No IP presents more than 5 distinct user agents"
fi

# ── 5. Claimed search-engine bots: are they genuine? ─────────────────────────
section "Claimed crawler traffic (share + spot-check)"
dump | awk -F'"' "$AWK_COMBINED"'{u=tolower(ua());
  if (u ~ /googlebot/) b="Googlebot"; else if (u ~ /bingbot/) b="Bingbot";
  else if (u ~ /gptbot|claudebot|ccbot|bytespider|amazonbot|petalbot|semrush|ahrefs|mj12bot|dotbot/) b="other-known-bot";
  else next; print b }' | sort | uniq -c | sort -rn \
  | awk -v t="$TOTAL" '{ printf "  %8d (%5.1f%%)  %s\n", $1, 100*$1/t, $2 }'
# Fake Googlebots are common. Verify the heaviest claimed-Googlebot IP by
# reverse DNS: real ones resolve to *.googlebot.com / *.google.com.
GBIP=$(dump | awk -F'"' "$AWK_COMBINED"'{ if (tolower(ua()) ~ /googlebot/) print ip() }' | sort | uniq -c | sort -rn | head -1 | awk '{print $2}')
if [ -n "${GBIP:-}" ]; then
  RDNS=$(host "$GBIP" 2>/dev/null | awk '/pointer/ {print $NF; exit}')
  case "${RDNS:-}" in
    *.googlebot.com.|*.google.com.) ok "Heaviest claimed Googlebot ($GBIP) reverse-resolves to $RDNS — genuine" ;;
    "") warn "Heaviest claimed Googlebot ($GBIP) has NO reverse DNS — likely fake; verify: host $GBIP" ;;
    *)  warn "Heaviest claimed Googlebot ($GBIP) reverse-resolves to '$RDNS' (not google) — impostor" ;;
  esac
fi

# ── 6. Status codes & scanner behaviour ──────────────────────────────────────
section "Status code distribution"
dump | awk -F'"' "$AWK_COMBINED"'{print status()}' | sort | uniq -c | sort -rn \
  | awk -v t="$TOTAL" '{ printf "  %8d (%5.1f%%)  %s\n", $1, 100*$1/t, $2 }'

section "Top 404 targets (vuln scanning shows up here)"
dump | awk -F'"' "$AWK_COMBINED"'{ if (status()==404) print path() }' | sort | uniq -c | sort -rn | head -n "$TOP_N" \
  | awk '{ printf "  %6d  %s\n", $1, $2 }'

section "Probes for known-exploit paths"
PROBES=$(dump | awk -F'"' "$AWK_COMBINED"'{ p=tolower(path());
  if (p ~ /wp-login|wp-admin|xmlrpc\.php|\.php$|\.env|\.git|phpmyadmin|\/cgi-bin|\.aws|\/vendor\/|\/config\.|\/backup|\/actuator|\/\.ssh/)
    print ip() }' | sort | uniq -c | sort -rn)
if [ -n "$PROBES" ]; then
  NPROBE=$(printf '%s\n' "$PROBES" | awk '{s+=$1} END {print s}')
  warn "$NPROBE requests probing for WordPress/PHP/.env/.git/etc. — background scanner noise; top offenders:"
  printf '%s\n' "$PROBES" | head -10 | awk '{ printf "  %6d  %s\n", $1, $2 }'
  info "Normal for any public host. Only worrying if these get 200s — check above that they all 404."
else
  ok "No requests for known-exploit paths"
fi

# ── 7. Write traffic: is anyone hammering the forms? ─────────────────────────
section "POST/PUT/DELETE activity"
dump | awk -F'"' "$AWK_COMBINED"'{ m=method(); if (m=="POST"||m=="PUT"||m=="DELETE") print m, path() }' \
  | sort | uniq -c | sort -rn | head -n "$TOP_N" \
  | awk '{ printf "  %6d  %-6s %s\n", $1, $2, $3 }'
TOPPOSTER=$(dump | awk -F'"' "$AWK_COMBINED"'{ if (method()=="POST") print ip() }' | sort | uniq -c | sort -rn | head -1 | awk '{print $1, $2}')
if [ -n "$TOPPOSTER" ]; then
  NPOSTS=${TOPPOSTER%% *}
  if [ "$NPOSTS" -ge 100 ]; then
    warn "Heaviest POSTer: $TOPPOSTER — check whether these are login attempts or form spam"
  else
    ok "Heaviest single-IP POST count: ${NPOSTS} — unremarkable"
  fi
else
  ok "No write-method requests at all"
fi

# ── 8. Where is the traffic landing? ─────────────────────────────────────────
section "Top $TOP_N requested paths"
dump | awk -F'"' "$AWK_COMBINED"'{print path()}' | sort | uniq -c | sort -rn | head -n "$TOP_N" \
  | awk -v t="$TOTAL" '{ printf "  %8d (%5.1f%%)  %s\n", $1, 100*$1/t, $2 }'

section "Top external referrers"
dump | awk -F'"' "$AWK_COMBINED"'{ rf=referer();
  if (rf=="-" || rf=="" || rf ~ /diytracker\.ch/) next;
  sub(/^https?:\/\//, "", rf); sub(/\/.*/, "", rf); print rf }' \
  | sort | uniq -c | sort -rn | head -n "$TOP_N" \
  | awk '{ printf "  %6d  %s\n", $1, $2 }'
info "Real audience growth usually has a story here (a post, a zine, a search-ranking jump)."
info "Referrer-less growth with browser UAs at human rates can still be legit (apps, direct, privacy browsers)."

# ── Summary ──────────────────────────────────────────────────────────────────
section "Summary"
printf '  %d ok, %d warnings\n' "$PASS" "$WARN"
if [ "$WARN" -eq 0 ]; then
  printf '  Nothing suspicious found: the growth pattern looks like real visitors.\n'
else
  printf '  Review warnings above. Quick levers if it is abuse: fail2ban nginx jail,\n'
  printf '  nginx limit_req on "location /", or a UFW block for specific IPs.\n'
fi
