(() => {
    const TOKEN_KEY = "atlas_jwt_token";
    let jwtToken = localStorage.getItem(TOKEN_KEY) || "";
    let currentUser = null;
    let activeConversationId = null;
    let activeDocumentId = null;
    let conversationsCache = [];

    // Auth Elements
    const authScreen = document.getElementById("auth-screen");
    const appWorkspace = document.getElementById("app-workspace");
    const userBar = document.getElementById("user-bar");
    const userEmailBadge = document.getElementById("user-email-badge");
    const logoutBtn = document.getElementById("logout-btn");
    const showLoginTab = document.getElementById("show-login-tab");
    const showSignupTab = document.getElementById("show-signup-tab");
    const loginForm = document.getElementById("login-form");
    const signupForm = document.getElementById("signup-form");
    const authStatus = document.getElementById("auth-status");

    // Sidebar & Chat Elements
    const chatSidebar = document.getElementById("chat-sidebar");
    const conversationList = document.getElementById("conversation-list");
    const newDocChatBtn = document.getElementById("new-doc-chat-btn");
    const emptyUploadBtn = document.getElementById("empty-upload-btn");
    const toggleSidebarBtn = document.getElementById("toggle-sidebar-btn");
    const activeDocTitle = document.getElementById("active-doc-title");
    const emptyChatState = document.getElementById("empty-chat-state");
    const chatHistory = document.getElementById("chat-history");
    const chatForm = document.getElementById("chat-form");
    const chatInput = document.getElementById("chat-input");
    const chatSubmit = document.getElementById("chat-submit");
    const sessionBadge = document.getElementById("session-badge");
    const inlineUploadBtn = document.getElementById("inline-upload-btn");
    const inlineDocFile = document.getElementById("inline-doc-file");
    const inlineUploadStatus = document.getElementById("inline-upload-status");

    function authHeaders(extra = {}) {
        const headers = { ...extra };
        if (jwtToken) headers["Authorization"] = `Bearer ${jwtToken}`;
        return headers;
    }

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = str ?? "";
        return div.innerHTML;
    }

    function scrollChatToBottom() {
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function setAuthError(msg) {
        if (!msg) {
            authStatus.classList.add("hidden");
            authStatus.textContent = "";
            return;
        }
        authStatus.textContent = msg;
        authStatus.classList.remove("hidden");
    }

    function setChatEnabled(enabled) {
        chatInput.disabled = !enabled;
        chatSubmit.disabled = !enabled;
        emptyChatState.classList.toggle("hidden", enabled);
        chatHistory.classList.toggle("hidden", !enabled);
    }

    function enterWorkspace(user) {
        currentUser = user;
        userEmailBadge.textContent = user.email;
        userBar.classList.remove("hidden");
        authScreen.classList.add("hidden");
        appWorkspace.classList.remove("hidden");
        setAuthError("");
        loadConversations(true);
        loadDocuments();
    }

    function exitWorkspace() {
        jwtToken = "";
        currentUser = null;
        activeConversationId = null;
        activeDocumentId = null;
        conversationsCache = [];
        localStorage.removeItem(TOKEN_KEY);
        userBar.classList.add("hidden");
        appWorkspace.classList.add("hidden");
        authScreen.classList.remove("hidden");
        activeDocTitle.textContent = "No Document Selected";
        sessionBadge.textContent = "Upload a document to begin a conversation";
        chatHistory.innerHTML = "";
        setChatEnabled(false);
    }

    async function apiFetch(url, options = {}) {
        const res = await fetch(url, {
            ...options,
            headers: authHeaders(options.headers || {}),
        });
        if (res.status === 401) {
            exitWorkspace();
            throw new Error("Session expired. Please sign in again.");
        }
        return res;
    }

    // Auth Tabs & Forms
    showLoginTab.addEventListener("click", () => {
        showLoginTab.classList.add("active");
        showSignupTab.classList.remove("active");
        loginForm.classList.remove("hidden");
        signupForm.classList.add("hidden");
        setAuthError("");
    });

    showSignupTab.addEventListener("click", () => {
        showSignupTab.classList.add("active");
        showLoginTab.classList.remove("active");
        signupForm.classList.remove("hidden");
        loginForm.classList.add("hidden");
        setAuthError("");
    });

    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const btn = document.getElementById("login-submit");
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span>Signing in...';
        setAuthError("");
        try {
            const res = await fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    email: document.getElementById("login-email").value.trim(),
                    password: document.getElementById("login-password").value,
                }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Login failed");
            jwtToken = data.access_token;
            localStorage.setItem(TOKEN_KEY, jwtToken);
            loginForm.reset();
            enterWorkspace(data.user);
        } catch (err) {
            setAuthError(err.message);
        } finally {
            btn.disabled = false;
            btn.textContent = "Sign In";
        }
    });

    signupForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const btn = document.getElementById("signup-submit");
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span>Creating account...';
        setAuthError("");
        try {
            const res = await fetch("/api/auth/signup", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    full_name: document.getElementById("signup-name").value.trim(),
                    email: document.getElementById("signup-email").value.trim(),
                    password: document.getElementById("signup-password").value,
                }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Sign up failed");
            jwtToken = data.access_token;
            localStorage.setItem(TOKEN_KEY, jwtToken);
            signupForm.reset();
            enterWorkspace(data.user);
        } catch (err) {
            setAuthError(err.message);
        } finally {
            btn.disabled = false;
            btn.textContent = "Create Account";
        }
    });

    logoutBtn.addEventListener("click", exitWorkspace);
    toggleSidebarBtn.addEventListener("click", () => {
        chatSidebar.classList.toggle("collapsed-mobile");
    });

    // Conversation Sidebar & Per-Document History Loading
    function renderSidebar() {
        if (!conversationsCache.length) {
            conversationList.innerHTML = '<p class="muted center" style="padding: 1rem;">No document chats yet. Click + New Doc to upload.</p>';
            return;
        }

        conversationList.innerHTML = conversationsCache.map((c) => {
            const isActive = String(c.id) === String(activeConversationId);
            const msgLabel = `${c.message_count || 0} msgs`;
            return `
                <div class="conv-item ${isActive ? "active" : ""}" data-conv-id="${escapeHtml(c.id)}">
                    <div class="conv-info" onclick="window.__selectConv('${escapeHtml(c.id)}')">
                        <div class="conv-title" title="${escapeHtml(c.title)}">📄 ${escapeHtml(c.title)}</div>
                        <div class="conv-meta">${escapeHtml(c.document_status)} &bull; ${escapeHtml(msgLabel)}</div>
                    </div>
                    <button class="conv-del-btn" type="button" title="Delete document & chat" onclick="window.__deleteDoc('${escapeHtml(c.document_id)}', event)">🗑</button>
                </div>
            `;
        }).join("");
    }

    async function loadConversations(autoSelect = false) {
        try {
            const res = await apiFetch("/api/conversations");
            const data = await res.json();
            conversationsCache = data.conversations || [];
            renderSidebar();

            if (!conversationsCache.length) {
                activeConversationId = null;
                activeDocumentId = null;
                activeDocTitle.textContent = "No Document Selected";
                sessionBadge.textContent = "Upload a document to begin a conversation";
                chatHistory.innerHTML = "";
                setChatEnabled(false);
                return;
            }

            if (autoSelect && (!activeConversationId || !conversationsCache.some((c) => String(c.id) === String(activeConversationId)))) {
                await selectConversation(String(conversationsCache[0].id));
            }
        } catch (err) {
            console.error("Failed loading conversations:", err);
        }
    }

    async function selectConversation(convId) {
        activeConversationId = convId;
        renderSidebar();
        setChatEnabled(true);
        chatHistory.innerHTML = '<div class="message assistant loading"><div class="loader-row"><span class="spinner accent"></span><span>Loading previous conversation...</span></div></div>';

        try {
            const res = await apiFetch(`/api/chat/history/${convId}`);
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Failed to load conversation");

            activeDocumentId = data.document_id;
            activeDocTitle.textContent = `📄 ${data.title}`;
            sessionBadge.textContent = `Isolated chat for this document (${data.document_status})`;
            chatHistory.innerHTML = "";

            const msgs = data.messages || [];
            if (msgs.length === 0) {
                appendMessage(
                    "assistant",
                    `Document "${data.title}" is loaded in this dedicated conversation. Ask any question about it!`,
                    { allowFeedback: false }
                );
            } else {
                msgs.forEach((m) => {
                    appendMessage(m.role, m.content, { allowFeedback: m.role === "assistant" });
                });
            }
        } catch (err) {
            chatHistory.innerHTML = "";
            appendMessage("assistant", `Error loading history: ${err.message}`, { status: "blocked" });
        }
    }

    window.__selectConv = (convId) => {
        selectConversation(convId);
    };

    // Safe XSS-escaped Markdown formatter for assistant replies
    function formatMarkdown(rawText) {
        let html = escapeHtml(rawText || "");

        // Fenced code blocks ```code```
        html = html.replace(/```(?:\w+)?\n?([\s\S]*?)```\n?/g, (_, code) => {
            return `<pre class="md-pre"><code>${code.trim()}</code></pre>`;
        });

        // Inline code `code`
        html = html.replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>');

        // Headings ### / ## / #
        html = html.replace(/^###\s+(.+)\n?/gm, '<h4 class="md-h">$1</h4>');
        html = html.replace(/^##\s+(.+)\n?/gm, '<h3 class="md-h">$1</h3>');
        html = html.replace(/^#\s+(.+)\n?/gm, '<h3 class="md-h">$1</h3>');

        // Bold **text** and __text__
        html = html.replace(/\*\*([^\n*][\s\S]*?[^\n*]?)\*\*/g, "<strong>$1</strong>");
        html = html.replace(/__([^\n_][\s\S]*?[^\n_]?)__/g, "<strong>$1</strong>");

        // Italic *text*
        html = html.replace(/(^|[^\w*])\*([^\s*][^\n*]*?)\*(?!\*)/g, "$1<em>$2</em>");

        // Clean bullet list lines starting with "* " or "- "
        html = html.replace(/^[ \t]*[\*\-]\s+(.+)\n?/gm, '<div class="md-li">&bull; $1</div>');

        return html;
    }

    // Chat Message Rendering & Loader
    function appendMessage(role, text, options = {}) {
        const wrapper = document.createElement("div");
        wrapper.className = `message ${role} ${options.status || ""}`.trim();

        const meta = document.createElement("div");
        meta.className = "msg-meta";
        meta.textContent = role === "user" ? (currentUser?.email || "You") : "Atlas AI";
        wrapper.appendChild(meta);

        const body = document.createElement("div");
        body.className = "msg-body";
        if (role === "assistant") {
            body.innerHTML = formatMarkdown(text);
        } else {
            body.textContent = text;
        }
        wrapper.appendChild(body);

        if (options.citations && options.citations.length > 0) {
            const citBox = document.createElement("div");
            citBox.className = "citations";
            options.citations.forEach((c) => {
                const chip = document.createElement("span");
                chip.className = "citation-chip";
                chip.title = c.snippet || "";
                chip.textContent = `[${c.index}] ${c.title} (p.${c.page}) • ${c.score}`;
                citBox.appendChild(chip);
            });
            wrapper.appendChild(citBox);
        }

        if (role === "assistant" && options.allowFeedback) {
            const fbRow = document.createElement("div");
            fbRow.className = "feedback-row";

            const upBtn = document.createElement("button");
            upBtn.className = "fb-btn";
            upBtn.type = "button";
            upBtn.textContent = "👍 Helpful";
            upBtn.onclick = () => sendFeedback(1, upBtn, downBtn);

            const downBtn = document.createElement("button");
            downBtn.className = "fb-btn";
            downBtn.type = "button";
            downBtn.textContent = "👎 Needs work";
            downBtn.onclick = () => sendFeedback(-1, downBtn, upBtn);

            fbRow.append(upBtn, downBtn);
            wrapper.appendChild(fbRow);
        }

        chatHistory.appendChild(wrapper);
        scrollChatToBottom();
    }

    function showChatLoader() {
        const loader = document.createElement("div");
        loader.id = "chat-loader-bubble";
        loader.className = "message assistant loading";
        loader.innerHTML = `
            <div class="msg-meta">Atlas AI</div>
            <div class="loader-row">
                <span class="spinner accent"></span>
                <span>Thinking &amp; searching this document</span>
                <span class="dot-anim"><span>.</span><span>.</span><span>.</span></span>
            </div>
        `;
        chatHistory.appendChild(loader);
        scrollChatToBottom();
        return loader;
    }

    async function sendFeedback(rating, activeBtn, otherBtn) {
        try {
            await apiFetch("/api/feedback", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ rating, note: "UI rating" }),
            });
            activeBtn.classList.add("voted");
            otherBtn.classList.remove("voted");
        } catch (err) {
            console.error("Feedback error:", err);
        }
    }

    // Workspace Tabs
    function switchTab(tabId) {
        document.querySelectorAll("#app-workspace .tabs .tab-btn").forEach((b) => {
            b.classList.toggle("active", b.dataset.tab === tabId);
        });
        document.querySelectorAll(".tab-panel").forEach((p) => {
            p.classList.toggle("active", p.id === tabId);
        });
        if (tabId === "docs-panel") loadDocuments();
        if (tabId === "approvals-panel") loadApprovals();
        if (tabId === "metrics-panel") loadMetrics();
    }

    document.querySelectorAll("#app-workspace .tabs .tab-btn").forEach((btn) => {
        btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });

    // Chat Form Submit (Scoped strictly to activeConversationId / activeDocumentId)
    chatForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const question = chatInput.value.trim();
        if (!question || !activeConversationId) return;

        appendMessage("user", question);
        chatInput.value = "";
        chatInput.disabled = true;
        chatSubmit.disabled = true;
        chatSubmit.innerHTML = '<span class="spinner"></span>Sending...';

        const loaderEl = showChatLoader();

        try {
            const res = await apiFetch("/api/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    question,
                    conversation_id: activeConversationId,
                    document_id: activeDocumentId,
                }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Chat request failed");

            loaderEl.remove();
            appendMessage("assistant", data.answer, {
                citations: data.citations || [],
                status: data.status,
                allowFeedback: data.status === "completed",
            });
            await loadConversations(false);
        } catch (err) {
            loaderEl.remove();
            appendMessage("assistant", `Error: ${err.message}`, { status: "blocked" });
        } finally {
            chatInput.disabled = false;
            chatSubmit.disabled = false;
            chatSubmit.textContent = "Send";
            chatInput.focus();
        }
    });

    // Shared Upload & Ingest -> Always Creates a NEW Dedicated Document Conversation
    function renderStatusBanner(el, msg, isLoading = false, isError = false) {
        el.classList.toggle("error", isError);
        el.innerHTML = isLoading
            ? `<span class="spinner accent"></span><span>${escapeHtml(msg)}</span>`
            : `<span>${escapeHtml(msg)}</span>`;
        el.classList.remove("hidden");
    }

    async function uploadAndCreateConversation(file, title, statusEl) {
        renderStatusBanner(statusEl, `1/3 Requesting upload URL for "${title}"...`, true);
        const urlResp = await apiFetch("/api/documents/upload-url", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                filename: file.name,
                title: title,
                content_type: file.type || "application/octet-stream",
            }),
        });
        const urlData = await urlResp.json();
        if (!urlResp.ok) throw new Error(urlData.detail || "Failed to initiate upload");

        renderStatusBanner(statusEl, `2/3 Uploading "${file.name}" (${urlData.backend})...`, true);
        const putHeaders = { ...(urlData.headers || { "Content-Type": file.type || "application/octet-stream" }) };
        const putOptions = {
            method: urlData.method || "PUT",
            headers: urlData.backend === "s3" ? putHeaders : authHeaders(putHeaders),
            body: file,
        };
        const putResp = await fetch(urlData.upload_url, putOptions);
        if (!putResp.ok) throw new Error("Failed uploading document bytes");

        renderStatusBanner(statusEl, "3/3 Extracting text, embedding, and creating new conversation...", true);
        const ingestResp = await apiFetch(`/api/documents/${urlData.document_id}/ingest?sync=true`, {
            method: "POST",
        });
        const ingestData = await ingestResp.json();
        if (!ingestResp.ok) throw new Error(ingestData.detail || "Ingestion failed");

        renderStatusBanner(statusEl, `Ready! Created new conversation for "${title}".`, false);

        // Refresh sidebar and immediately switch to the NEW document conversation
        await loadConversations(false);
        await selectConversation(ingestData.conversation_id);
        await loadDocuments();
        return ingestData;
    }

    // Trigger file picker from Sidebar "+ New Doc", Empty State button, or Chat bar "Upload New Doc"
    [newDocChatBtn, emptyUploadBtn, inlineUploadBtn].forEach((btn) => {
        btn.addEventListener("click", () => inlineDocFile.click());
    });

    inlineDocFile.addEventListener("change", async () => {
        const file = inlineDocFile.files[0];
        if (!file) return;
        inlineUploadBtn.disabled = true;
        newDocChatBtn.disabled = true;
        inlineUploadBtn.innerHTML = '<span class="spinner accent"></span>Uploading...';
        try {
            await uploadAndCreateConversation(file, file.name, inlineUploadStatus);
        } catch (err) {
            renderStatusBanner(inlineUploadStatus, `Upload error: ${err.message}`, false, true);
        } finally {
            inlineDocFile.value = "";
            inlineUploadBtn.disabled = false;
            newDocChatBtn.disabled = false;
            inlineUploadBtn.textContent = "📎 Upload New Doc";
        }
    });

    // Documents & Upload Tab Form
    const uploadForm = document.getElementById("upload-form");
    const uploadStatus = document.getElementById("upload-status");
    const uploadSubmit = document.getElementById("upload-submit");

    uploadForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fileEl = document.getElementById("doc-file");
        const titleEl = document.getElementById("doc-title");
        const file = fileEl.files[0];
        if (!file) return;

        uploadSubmit.disabled = true;
        uploadSubmit.innerHTML = '<span class="spinner"></span>Processing...';
        try {
            await uploadAndCreateConversation(file, titleEl.value.trim(), uploadStatus);
            uploadForm.reset();
            switchTab("chat-panel");
        } catch (err) {
            renderStatusBanner(uploadStatus, `Error: ${err.message}`, false, true);
        } finally {
            uploadSubmit.disabled = false;
            uploadSubmit.textContent = "Upload & Start Chat";
        }
    });

    async function loadDocuments() {
        const tbody = document.getElementById("docs-tbody");
        try {
            const res = await apiFetch("/api/documents");
            const data = await res.json();
            const docs = data.documents || [];

            if (docs.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" class="muted center">You have no uploaded documents yet.</td></tr>';
                return;
            }

            tbody.innerHTML = docs.map((d) => `
                <tr>
                    <td><strong>${escapeHtml(d.title)}</strong></td>
                    <td><span class="badge ${escapeHtml(d.status)}">${escapeHtml(d.status)}</span></td>
                    <td>v${escapeHtml(String(d.current_version))}</td>
                    <td class="muted">${escapeHtml(new Date(d.created_at).toLocaleString())}</td>
                    <td>
                        <button class="btn secondary small" onclick="window.__openDocChat('${escapeHtml(d.id)}')">Open Chat</button>
                        <button class="btn danger small" onclick="window.__deleteDoc('${escapeHtml(d.id)}')">Delete</button>
                    </td>
                </tr>
            `).join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="5" class="muted center">Error loading documents: ${escapeHtml(err.message)}</td></tr>`;
        }
    }

    window.__openDocChat = async (docId) => {
        const conv = conversationsCache.find((c) => String(c.document_id) === String(docId));
        switchTab("chat-panel");
        if (conv) {
            await selectConversation(String(conv.id));
        }
    };

    window.__deleteDoc = async (docId, event) => {
        if (event) event.stopPropagation();
        try {
            const res = await apiFetch(`/api/documents/${docId}`, { method: "DELETE" });
            if (!res.ok) {
                const err = await res.json();
                alert(err.detail || "Delete failed");
                return;
            }
            if (String(activeDocumentId) === String(docId)) {
                activeConversationId = null;
                activeDocumentId = null;
            }
            await loadConversations(true);
            await loadDocuments();
        } catch (err) {
            alert(`Delete failed: ${err.message}`);
        }
    };

    async function loadApprovals() {
        const container = document.getElementById("approvals-list");
        try {
            const res = await apiFetch("/api/approvals");
            const data = await res.json();
            const items = data.approvals || [];

            if (items.length === 0) {
                container.innerHTML = '<p class="muted">No HITL approval requests found.</p>';
                return;
            }

            container.innerHTML = items.map((a) => `
                <div class="approval-card">
                    <div>
                        <span class="badge ${escapeHtml(a.status)}">${escapeHtml(a.status)}</span>
                        <strong>${escapeHtml(a.requester)}</strong> asked:
                        <p style="margin-top: 0.35rem;">${escapeHtml(a.question)}</p>
                        <div class="muted" style="margin-top: 0.25rem;">ID: ${escapeHtml(a.id)} ${a.reviewer ? `• Reviewed by ${escapeHtml(a.reviewer)}` : ""}</div>
                    </div>
                    ${a.status === "pending" ? `
                        <div class="approval-actions">
                            <button class="btn success" onclick="window.__decideApproval(this, '${escapeHtml(a.id)}', 'approved')">Approve &amp; Execute</button>
                            <button class="btn danger" onclick="window.__decideApproval(this, '${escapeHtml(a.id)}', 'rejected')">Reject</button>
                        </div>
                    ` : ""}
                </div>
            `).join("");
        } catch (err) {
            container.innerHTML = `<p class="muted">Error loading approvals: ${escapeHtml(err.message)}</p>`;
        }
    }

    window.__decideApproval = async (btnEl, approvalId, decision) => {
        const origText = btnEl.textContent;
        btnEl.disabled = true;
        btnEl.innerHTML = '<span class="spinner"></span>Working...';
        try {
            const res = await apiFetch(`/api/approvals/${approvalId}/decision`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ decision }),
            });
            const data = await res.json();
            if (!res.ok) {
                alert(data.detail || "Decision failed");
                return;
            }
            if (data.deleted_document_id && String(activeDocumentId) === String(data.deleted_document_id)) {
                activeConversationId = null;
                activeDocumentId = null;
            }
            if (decision === "approved" && data.rag_result && !data.deleted_document_id) {
                appendMessage("assistant", `[Approved HITL Request]\n${data.rag_result.answer}`, {
                    citations: data.rag_result.citations || [],
                    status: "completed",
                    allowFeedback: true,
                });
            }
            await loadApprovals();
            await loadConversations(true);
            await loadDocuments();
        } catch (err) {
            alert(`Error: ${err.message}`);
        } finally {
            btnEl.disabled = false;
            btnEl.textContent = origText;
        }
    };

    async function loadMetrics() {
        const grid = document.getElementById("kpi-grid");
        const auditBody = document.getElementById("audit-tbody");
        try {
            const res = await apiFetch("/api/metrics");
            const m = await res.json();

            const cards = [
                { label: "My Documents", value: m.total_documents ?? 0 },
                { label: "Ready Docs", value: (m.documents && m.documents.ready) || 0 },
                { label: "Chat Messages", value: m.total_messages ?? 0 },
                { label: "Pending HITL", value: (m.approvals && m.approvals.pending) || 0 },
                { label: "Upvotes (+1)", value: (m.feedback && m.feedback["1"]) || 0 },
            ];
            grid.innerHTML = cards.map((c) => `
                <div class="kpi-card">
                    <div class="kpi-label">${escapeHtml(c.label)}</div>
                    <div class="kpi-value">${escapeHtml(String(c.value))}</div>
                </div>
            `).join("");

            const events = m.recent_audit || [];
            if (events.length === 0) {
                auditBody.innerHTML = '<tr><td colspan="4" class="muted center">No audit events recorded yet.</td></tr>';
                return;
            }
            auditBody.innerHTML = events.map((ev) => `
                <tr>
                    <td>${escapeHtml(new Date(ev.created_at).toLocaleString())}</td>
                    <td>${escapeHtml(ev.actor)}</td>
                    <td><strong>${escapeHtml(ev.action)}</strong></td>
                    <td class="muted">${escapeHtml(JSON.stringify(ev.detail))}</td>
                </tr>
            `).join("");
        } catch (err) {
            grid.innerHTML = `<p class="muted">Error loading metrics: ${escapeHtml(err.message)}</p>`;
        }
    }

    document.getElementById("refresh-docs-btn").addEventListener("click", loadDocuments);
    document.getElementById("refresh-approvals-btn").addEventListener("click", loadApprovals);
    document.getElementById("refresh-metrics-btn").addEventListener("click", loadMetrics);

    async function init() {
        if (!jwtToken) {
            exitWorkspace();
            return;
        }
        try {
            const res = await fetch("/api/auth/me", { headers: authHeaders() });
            if (!res.ok) throw new Error("Invalid token");
            const user = await res.json();
            enterWorkspace(user);
        } catch {
            exitWorkspace();
        }
    }

    init();
})();
