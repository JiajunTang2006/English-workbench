    // ================= TeachMate Fetch SSE 客户端 =================
    // 使用 fetch() + Authorization:Bearer + ReadableStream 解析 SSE。
    // 不使用原生 EventSource（避免 Token 放进 URL）。
    // 事件 seq 保存游标，断线从 after 恢复。
    // 15 秒无事件视为连接超时，自动重连。
    // SSE 不可用时回退轮询。
    // 页面切换或取消时通过 AbortController 关闭连接。

    const teachMateEvents = (function () {
      const BASE = '/api/v1/agent';
      const HEARTBEAT_TIMEOUT_MS = 15000;
      const RECONNECT_DELAY_MS = 2000;
      const MAX_RECONNECT_ATTEMPTS = 5;

      function _token() {
        return (typeof workbenchToken !== 'undefined') ? (workbenchToken || '') : '';
      }

      /**
       * SSEClient —— 管理单个 run 的事件流连接。
       *
       * @param {number} runId
       * @param {function} onEvent — 回调 (eventObj) => void，eventObj = { seq, event, timestamp, data }
       * @param {function} onError — 回调 (err) => void，SSE 不可用时触发（调用方应回退轮询）
       * @param {function} onTerminal — 回调 (eventObj) => void，收到终态事件时触发
       */
      class SSEClient {
        constructor(runId, onEvent, onError, onTerminal) {
          this._runId = runId;
          this._onEvent = onEvent || function () {};
          this._onError = onError || function () {};
          this._onTerminal = onTerminal || function () {};
          this._lastSeq = 0;
          this._abortCtrl = null;
          this._heartbeatTimer = null;
          this._reconnectCount = 0;
          this._closed = false;
          this._terminalEventTypes = new Set([
            'run.completed', 'run.failed', 'run.cancelled', 'run.degraded',
          ]);
        }

        /**
         * 启动 SSE 连接。从 _lastSeq 之后开始获取事件。
         */
        async start() {
          if (this._closed) return;
          this._abortCtrl = new AbortController();

          var url = BASE + '/runs/' + this._runId + '/events?after=' + this._lastSeq;
          var headers = {
            'Accept': 'text/event-stream',
            'Authorization': 'Bearer ' + _token(),
            'Cache-Control': 'no-cache',
          };

          try {
            var response = await fetch(url, {
              method: 'GET',
              headers: headers,
              signal: this._abortCtrl.signal,
            });

            if (!response.ok) {
              this._onError(new Error('SSE HTTP ' + response.status));
              return;
            }

            var contentType = response.headers.get('content-type') || '';
            if (contentType.indexOf('text/event-stream') < 0) {
              this._onError(new Error('Server did not return text/event-stream'));
              return;
            }

            this._reconnectCount = 0;
            this._startHeartbeat();

            var reader = response.body.getReader();
            var decoder = new TextDecoder();
            var buffer = '';

            try {
              while (!this._closed) {
                var chunk = await reader.read();
                if (chunk.done) break;
                // 任何到达的字节都代表连接存活：后端在长任务中会发送
                // ": keep-alive" 注释帧，必须据此刷新心跳，否则注释帧
                // 不解出 data: 事件、不会触发 _processFrame 里的刷新，
                // 15s 无事件时仍会被误判为超时而重连/回退轮询。
                this._startHeartbeat();
                buffer += decoder.decode(chunk.value, { stream: true });

                var idx;
                while ((idx = buffer.indexOf('\n\n')) >= 0) {
                  var frame = buffer.substring(0, idx);
                  buffer = buffer.substring(idx + 2);
                  this._processFrame(frame);
                }
              }
            } catch (e) {
              if (e.name === 'AbortError') return;
              // 其他错误 → 尝试重连
            } finally {
              this._stopHeartbeat();
            }
          } catch (e) {
            if (e.name === 'AbortError') return;
            if (this._reconnectCount === 0) {
              this._onError(e);
              return;
            }
          }

          if (!this._closed) {
            this._scheduleReconnect();
          }
        }

        _processFrame(frame) {
          var lines = frame.split('\n');
          var dataLines = [];
          for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (line.indexOf('data:') === 0) {
              dataLines.push(line.substring(5).trim());
            }
          }
          if (dataLines.length === 0) return;
          var jsonStr = dataLines.join('\n');
          try {
            var eventObj = JSON.parse(jsonStr);
          } catch (e) {
            console.error('SSE parse error:', e, jsonStr);
            return;
          }

          if (typeof eventObj.seq === 'number' && eventObj.seq > this._lastSeq) {
            this._lastSeq = eventObj.seq;
          }

          this._startHeartbeat();
          this._onEvent(eventObj);

          if (this._terminalEventTypes.has(eventObj.event)) {
            this._onTerminal(eventObj);
            this.close();
          }
        }

        _startHeartbeat() {
          this._stopHeartbeat();
          var self = this;
          this._heartbeatTimer = setTimeout(function () {
            if (!self._closed && self._abortCtrl) {
              self._abortCtrl.abort();
              self._abortCtrl = null;
            }
          }, HEARTBEAT_TIMEOUT_MS);
        }

        _stopHeartbeat() {
          if (this._heartbeatTimer) {
            clearTimeout(this._heartbeatTimer);
            this._heartbeatTimer = null;
          }
        }

        _scheduleReconnect() {
          if (this._closed) return;
          this._reconnectCount++;
          if (this._reconnectCount > MAX_RECONNECT_ATTEMPTS) {
            this._onError(new Error('SSE 重连次数超限，回退轮询'));
            return;
          }
          var delay = RECONNECT_DELAY_MS * this._reconnectCount;
          var self = this;
          setTimeout(function () {
            if (!self._closed) self.start();
          }, delay);
        }

        close() {
          this._closed = true;
          this._stopHeartbeat();
          if (this._abortCtrl) {
            this._abortCtrl.abort();
            this._abortCtrl = null;
          }
        }

        get lastSeq() { return this._lastSeq; }
      }

      return { SSEClient: SSEClient };
    })();

    if (typeof window !== 'undefined') window.teachMateEvents = teachMateEvents;
