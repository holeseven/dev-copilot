const assert = require('assert');
const fs = require('fs');
const vm = require('vm');


class Element {
    constructor(tagName, id = '') {
        this.tagName = tagName;
        this.id = id;
        this.className = '';
        this.value = '';
        this.disabled = false;
        this.hidden = false;
        this.style = {};
        this.children = [];
        this.listeners = {};
        this.parentElement = null;
        this._textContent = '';
        this._innerHTML = '';
        this.scrollTop = 0;
        this.scrollHeight = 0;
    }

    get textContent() { return this._textContent; }
    set textContent(value) {
        this._textContent = String(value);
        this.children = [];
    }

    get innerHTML() { return this._innerHTML; }
    set innerHTML(value) {
        this._innerHTML = String(value);
        this.children = [];
        this._textContent = '';
    }

    appendChild(child) {
        child.parentElement = this;
        this.children.push(child);
        this.scrollHeight = this.children.length;
        return child;
    }

    addEventListener(name, callback) { this.listeners[name] = callback; }

    querySelector(selector) {
        return allElements(this).find(element => selector === `.${element.className}`) || null;
    }

    querySelectorAll(selector) {
        return allElements(this).filter(element => selector === `.${element.className}`);
    }

    remove() {
        if (!this.parentElement) return;
        this.parentElement.children = this.parentElement.children.filter(child => child !== this);
        this.parentElement = null;
    }
}


function allElements(root) {
    return root.children.flatMap(child => [child, ...allElements(child)]);
}


class ControlledReader {
    constructor() {
        this.pending = [];
        this.cancelCalls = 0;
    }

    read() {
        return new Promise(resolve => this.pending.push(resolve));
    }

    push(text) {
        const resolve = this.pending.shift();
        assert(resolve, 'reader must have a pending read');
        resolve({ done: false, value: new TextEncoder().encode(text) });
    }

    finish() {
        const resolve = this.pending.shift();
        assert(resolve, 'reader must have a pending read');
        resolve({ done: true, value: undefined });
    }

    async cancel() {
        this.cancelCalls += 1;
    }
}


function jsonResponse(body) {
    return { ok: true, status: 200, json: async () => body, text: async () => '' };
}


async function flush() {
    await new Promise(resolve => setImmediate(resolve));
}


async function main() {
    const ids = [
        'messages', 'user-input', 'send-btn', 'status-badge', 'workspace-select',
        'workspace-guide', 'repository-list', 'scan-summary', 'current-workspace',
        'workspace-id', 'workspace-name', 'repository-name', 'repository-path',
        'activate-btn', 'refresh-btn', 'create-workspace-btn', 'add-repository-btn',
        'scan-btn',
    ];
    const elements = Object.fromEntries(ids.map(id => [id, new Element('div', id)]));
    const document = {
        getElementById: id => elements[id],
        createElement: tagName => new Element(tagName),
        querySelectorAll: selector => Object.values(elements).flatMap(element => element.querySelectorAll(selector)),
    };

    const chatResponses = [];
    const chatSignals = [];
    const workspaces = {
        one: { id: 'one', name: 'One', repositories: [] },
        two: { id: 'two', name: 'Two', repositories: [] },
    };
    async function fetch(url, options = {}) {
        if (url === '/workspaces') return jsonResponse([]);
        if (url === '/chat') {
            chatSignals.push(options.signal);
            const reader = chatResponses.shift();
            assert(reader, 'chat response reader must be queued');
            return { ok: true, status: 200, body: { getReader: () => reader } };
        }
        if (url.startsWith('/tools?workspace_id=')) return jsonResponse({ total: 5, tools: [] });
        const match = url.match(/^\/workspaces\/(one|two)(?:\/activate)?$/);
        if (match) return jsonResponse(workspaces[match[1]]);
        throw new Error(`unexpected fetch: ${url}`);
    }

    const html = fs.readFileSync('engine/frontend/index.html', 'utf8');
    const start = html.indexOf('<script>') + '<script>'.length;
    const end = html.lastIndexOf('</script>');
    const context = vm.createContext({
        AbortController,
        TextDecoder,
        URL,
        console,
        document,
        fetch,
        prompt: () => '',
        setImmediate,
    });
    vm.runInContext(html.slice(start, end), context);
    await flush();

    await context.selectWorkspace('one');
    const oldReader = new ControlledReader();
    chatResponses.push(oldReader);
    const oldStream = context.startStreaming('old question');
    await flush();

    await context.selectWorkspace('two');
    assert.strictEqual(chatSignals[0].aborted, true, 'workspace switch aborts old fetch');
    assert.strictEqual(oldReader.cancelCalls, 1, 'workspace switch cancels old reader');
    assert.strictEqual(elements['status-badge'].textContent, '● 已连接');

    const newReader = new ControlledReader();
    chatResponses.push(newReader);
    const newStream = context.startStreaming('new question');
    await flush();

    oldReader.push(
        'event: tool\ndata: {"status":"start","tool_name":"old_tool"}\n\n' +
        'event: message\ndata: {"token":"OLD"}\n\n' +
        'event: approval_required\ndata: {"action":"old approval"}\n\n' +
        'event: error\ndata: {"message":"old error"}\n\n' +
        'event: done\ndata: {"status":"completed"}\n\n'
    );
    await flush();
    await oldStream;

    assert.strictEqual(elements['send-btn'].disabled, true, 'old finally does not unlock new stream');
    const afterOld = allElements(elements.messages).map(element => element.textContent).join('\n');
    assert(!afterOld.includes('OLD'));
    assert(!afterOld.includes('old_tool'));
    assert(!afterOld.includes('old approval'));
    assert(!afterOld.includes('old error'));
    assert(!allElements(elements.messages).some(element => element.className === 'approval-box'));

    newReader.push('event: message\ndata: {"token":"NEW"}\n\n');
    await flush();
    newReader.finish();
    await newStream;

    const finalMessages = allElements(elements.messages).map(element => element.textContent).join('\n');
    assert(finalMessages.includes('NEW'));
    assert(!finalMessages.includes('OLD'));
    assert.strictEqual(elements['send-btn'].disabled, false);
}


main().then(
    () => console.log('frontend stream switch isolation OK'),
    error => {
        console.error(error);
        process.exitCode = 1;
    },
);
