# Manual Testing Workflow: Virtual ASIN Race Condition Fix

## Prerequisites

1. **Start development environment:**
   ```bash
   cd /home/gary/abr-dev
   source .venv/bin/activate
   just dev  # Starts FastAPI dev server with migrations
   ```

2. **In another terminal, start CSS watcher:**
   ```bash
   cd /home/gary/abr-dev
   just tailwind
   ```

3. **Verify app is running:**
   - Open http://localhost:8000
   - Should see login page or search page

---

## Test 1: Normal Single Request (Baseline)

**Goal:** Verify virtual book upgrade works in normal conditions

### Steps:

1. **Set up test book (if not already in Prowlarr):**
   - Use a book NOT in Audible (e.g., obscure indie published book)
   - Example: "The Quantum Paradox" by "Dr. M. Strange"
   - Ensure it exists in your Prowlarr instance

2. **First search - creates virtual book:**
   ```bash
   curl -X GET "http://localhost:8000/api/search?q=The%20Quantum%20Paradox&source=prowlarr" \
     -H "Authorization: Bearer YOUR_API_KEY" \
     -s | jq '.'
   ```
   
   **Expected in response:**
   - ASIN format: `VIRTUAL-{11-char-hex}`
   - Example: `VIRTUAL-a3f9b8d2c1`

3. **Check logs for:**
   ```
   ✅ Generated virtual ASIN: VIRTUAL-a3f9b8d2c1
   ```

4. **Second search - upgrade attempt:**
   ```bash
   curl -X GET "http://localhost:8000/api/search?q=The%20Quantum%20Paradox&source=prowlarr" \
     -H "Authorization: Bearer YOUR_API_KEY" \
     -s | jq '.'
   ```

5. **Check logs for one of:**
   - `✅ UPGRADE FOUND:` - Successfully upgraded to real Audible book
   - `No upgrade found for virtual book:` - Still virtual (expected for indie books)

### Expected Result:
✅ No errors, graceful handling of virtual or real books

---

## Test 2: Concurrent Upgrade (Race Condition Test)

**Goal:** Verify concurrent requests don't cause primary key violations

### Steps:

1. **Prepare concurrent request script:**
   
   Create file `test-concurrent.sh`:
   ```bash
   #!/bin/bash
   
   API_KEY="YOUR_API_KEY"
   QUERY="The%20Quantum%20Paradox"
   BASE_URL="http://localhost:8000/api/search"
   
   # Fire 5 concurrent requests to same book
   for i in {1..5}; do
     (
       echo "[Request $i] Starting..."
       RESPONSE=$(curl -s -X GET "${BASE_URL}?q=${QUERY}&source=prowlarr" \
         -H "Authorization: Bearer ${API_KEY}")
       ASIN=$(echo "$RESPONSE" | jq -r '.results[0].asin // "UNKNOWN"')
       echo "[Request $i] Got ASIN: $ASIN"
     ) &
   done
   
   wait
   echo "All requests completed"
   ```

2. **Make executable and run:**
   ```bash
   chmod +x test-concurrent.sh
   ./test-concurrent.sh
   ```

3. **Watch FastAPI logs in first terminal:**
   - Look for database lock acquisition
   - Should see orderly queue formation
   - No IntegrityError exceptions (or graceful handling if they occur)

### Expected Behavior:

```
Request 1: 🔄 Checking for upgrade of virtual book...
           ↓ Acquires lock
           
Request 2: [waits for lock]
Request 3: [waits for lock]
Request 4: [waits for lock]
Request 5: [waits for lock]

Request 1: ✅ Upgraded virtual book → real book
           ↓ Releases lock
           
Request 2: [acquires lock, finds real book already exists]
           Graceful handling, returns real book
           ↓ Releases lock
           
[Repeat for remaining requests]
```

### What to Look For in Logs:

✅ **SUCCESS - No errors:**
```
✅ Upgraded virtual book VIRTUAL-xxx → B002V00TOO
✅ Upgraded virtual book VIRTUAL-xxx → B002V00TOO
✅ Upgraded virtual book VIRTUAL-xxx → B002V00TOO
```

✅ **EXPECTED - Graceful conflict handling:**
```
⚠️ Virtual book upgrade conflict (another request won the race)
⚠️ Virtual book upgrade conflict (another request won the race)
```

❌ **FAILURE - Primary key violation:**
```
ERROR: IntegrityError: UNIQUE constraint failed: audiobook.asin
[exception traceback]
```

---

## Test 3: Stress Test (Multiple Concurrent Users)

**Goal:** Verify locking works under sustained concurrent load

### Steps:

1. **Use Apache Bench for load testing:**
   ```bash
   # 20 concurrent requests, 100 total requests
   ab -n 100 -c 20 \
     -H "Authorization: Bearer YOUR_API_KEY" \
     "http://localhost:8000/api/search?q=The%20Quantum%20Paradox&source=prowlarr"
   ```

2. **Watch for:**
   - All requests complete successfully
   - No 500 errors (status codes should be 200)
   - Response times are reasonable (mostly <500ms)

3. **Check application logs:**
   - Should see queuing behavior (requests waiting for locks)
   - No deadlocks or hangs

### Expected Output:
```
Benchmarking localhost (be patient)...
Completed 10 requests
Completed 20 requests
...
Completed 100 requests

Finished 100 requests

...
Requests per second:    45.23 [#/sec]
Failed requests:        0
```

---

## Test 4: Verify Database State

**Goal:** Ensure no orphaned or corrupted records

### Steps:

1. **Query SQLite database directly:**
   ```bash
   sqlite3 /home/gary/abr-dev/config/abr.db
   ```

2. **Check for orphaned virtual books:**
   ```sql
   SELECT asin, title, author, downloaded FROM audiobook 
   WHERE asin LIKE 'VIRTUAL-%' 
   ORDER BY asin;
   ```
   
   **Expected:** Virtual books should have corresponding requests

3. **Check for primary key duplicates:**
   ```sql
   SELECT asin, COUNT(*) as count FROM audiobook 
   GROUP BY asin 
   HAVING count > 1;
   ```
   
   **Expected:** Empty result (no duplicates)

4. **Check request integrity:**
   ```sql
   SELECT ar.asin, ab.asin, ab.title 
   FROM audiobook_request ar
   LEFT JOIN audiobook ab ON ar.asin = ab.asin
   WHERE ab.asin IS NULL;
   ```
   
   **Expected:** Empty result (no orphaned requests)

5. **Exit SQLite:**
   ```sql
   .quit
   ```

---

## Test 5: Edge Cases

### Edge Case 1: Virtual Book Upgrade Failure

**Scenario:** Network error during upgrade check

1. **Simulate slow network:**
   - Use browser DevTools (F12 → Network tab)
   - Set network throttling to "Slow 3G"

2. **Search for book requiring upgrade:**
   ```bash
   curl -X GET "http://localhost:8000/api/search?q=TestBook" \
     -H "Authorization: Bearer YOUR_API_KEY"
   ```

3. **Expected:** Graceful timeout handling, returns existing virtual book

### Edge Case 2: Mixed Virtual/Real Book Results

**Scenario:** Same search returns both virtual and real books

1. **Search for popular book (exists in Audible):**
   ```bash
   curl -X GET "http://localhost:8000/api/search?q=The%20Hobbit" \
     -H "Authorization: Bearer YOUR_API_KEY" \
     -s | jq '.results[] | {asin, title}'
   ```

2. **Expected:** 
   - Real Audible books should have ASIN starting with `B`
   - If virtual books present, should have `VIRTUAL-` prefix
   - No mixing (no VIRTUAL- prefixed book with real ASIN)

### Edge Case 3: Concurrent Lock Timeout

**Scenario:** Very long lock hold time

1. **Monitor lock behavior with many requests:**
   ```bash
   # 50 concurrent requests
   ab -n 200 -c 50 \
     -H "Authorization: Bearer YOUR_API_KEY" \
     "http://localhost:8000/api/search?q=TestBook"
   ```

2. **Check response times:**
   - Some requests should queue and take longer
   - Max time should still be <5 seconds (not hanging)
   - No timeout errors

3. **Check logs for:**
   - Lock wait times increasing with concurrency
   - No deadlock detection

---

## Verification Checklist

After running all tests, verify:

- [ ] Single request test: No errors, book found or virtual ASIN generated
- [ ] Concurrent test: No primary key violation errors
- [ ] Stress test: All 100 requests succeeded (0 failed)
- [ ] Database integrity: No duplicate ASINs, no orphaned requests
- [ ] Lock behavior: Requests queue orderly (check logs)
- [ ] Edge cases: Graceful handling of failures/timeouts
- [ ] Logs: No unhandled exceptions, only expected warnings

---

## Troubleshooting

### Issue: 401 Unauthorized errors

**Solution:** Get valid API key
```bash
# Check .env.local or settings in admin UI
# Create new API key if needed
```

### Issue: Prowlarr not found (503 errors)

**Solution:** Verify Prowlarr configuration
```bash
# Check Prowlarr is running
# Verify ABR_PROWLARR__BASE_URL is set correctly
# Test: curl http://localhost:9696/api/v1/indexers (default Prowlarr port)
```

### Issue: FastAPI server crashes during tests

**Solution:** Check error logs
```bash
# Terminal 1 should show full traceback
# Look for IntegrityError or other database errors
# Rollback and try again
```

### Issue: Database locked errors

**Solution:** Close other connections
```bash
# Stop any running sqlite3 terminals
# Stop any other ABR instances
# Restart `just dev`
```

---

## Log Inspection Commands

### Filter for virtual book operations:
```bash
# Terminal where app is running, Ctrl+C to pause and view logs
# Or check app logs file if available

# Look for these patterns:
grep -i "virtual" /path/to/app.log
grep -i "upgrade" /path/to/app.log
grep -i "lock" /path/to/app.log
```

### Real-time log monitoring (if using file logging):
```bash
tail -f /path/to/app.log | grep -E "(Upgrade|virtual|lock)"
```

---

## Success Criteria

✅ **Test passes if:**
1. Single request: Book found or virtual ASIN generated
2. Concurrent requests: No primary key errors
3. Stress test: 100% success rate
4. Database: No corrupted/orphaned records
5. Logs: Ordered lock acquisition, no exceptions
6. Edge cases: Graceful error handling

❌ **Test fails if:**
- Primary key constraint violations
- IntegrityError exceptions (not gracefully handled)
- Database contains duplicate ASINs
- Timeout or hanging requests
- Unhandled exceptions in logs
