"use strict";

function byId(id) {
    return document.getElementById(id);
}

const ui = {
    ask: byId("ask"),
    quit: byId("quit"),
    question: byId("question"),
    model: byId("model"),
    topk: byId("topk"),
    searchMode: byId("searchMode"),
    think: byId("think"),
    contextChars: byId("contextChars"),
    numCtx: byId("numCtx"),
    numPredict: byId("numPredict"),
    temperature: byId("temperature"),
    status: byId("status"),
    resultPanel: byId("resultPanel"),
    errorPanel: byId("errorPanel"),
    answer: byId("answer"),
    details: byId("details"),
    metrics: byId("metrics"),
    error: byId("error"),
};

async function requestJson(url, options = {}) {
    const response = await fetch(url, { cache: "no-store", ...options });
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.error || `Request failed: ${response.status}`);
    }
    return data;
}

function setBusy(busy, message = "") {
    ui.ask.disabled = busy;
    ui.status.textContent = message;
}

function showError(error) {
    ui.error.textContent = String(error);
    ui.errorPanel.classList.remove("hidden");
}

function createMetric(name, value) {
    const box = document.createElement("div");
    box.className = "metric";

    const label = document.createElement("div");
    label.className = "metric-name";
    label.textContent = name;

    const metricValue = document.createElement("div");
    metricValue.className = "metric-value";
    metricValue.textContent = value;

    box.append(label, metricValue);
    return box;
}

function applyConfig(config) {
    ui.topk.value = config.default_top_k;
    ui.searchMode.value = config.default_search_mode;
    ui.think.value = config.default_think_mode || "auto";
    ui.contextChars.value = config.default_context_chars;
    ui.numCtx.value = config.default_num_ctx;
    ui.numPredict.value = config.default_num_predict;
    ui.temperature.value = config.default_temperature;
}

async function loadConfig() {
    const config = await requestJson("/api/config");
    applyConfig(config);
}

async function loadModels() {
    try {
        const data = await requestJson("/api/models");
        ui.model.replaceChildren();

        const defaultName = (data.default_model || "").toLowerCase();
        let selected = false;

        for (const name of data.models) {
            const option = document.createElement("option");
            option.value = name;
            option.textContent = name;

            const normalized = name.toLowerCase();
            if (
                !selected &&
                (
                    normalized === defaultName ||
                    normalized === `${defaultName}:latest` ||
                    normalized.startsWith(`${defaultName}:`)
                )
            ) {
                option.selected = true;
                selected = true;
            }

            ui.model.appendChild(option);
        }

        if (!selected && ui.model.options.length > 0) {
            ui.model.options[0].selected = true;
        }

        if (data.models.length === 0) {
            const option = document.createElement("option");
            option.value = "";
            option.textContent = "No Ollama models available";
            ui.model.appendChild(option);
        }
    } catch (error) {
        ui.model.replaceChildren();
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "Failed to load models";
        ui.model.appendChild(option);
        showError(error);
    }
}

async function openArticle(title) {
    try {
        await requestJson("/api/open_article", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title }),
        });
        ui.status.textContent = `Opened article viewer: ${title}`;
    } catch (error) {
        showError(error);
        ui.status.textContent = "Failed to open article viewer";
    }
}

function renderArticles(results) {
    ui.details.replaceChildren();
    const articles = new Map();

    for (const result of results) {
        if (!articles.has(result.title)) {
            articles.set(result.title, {
                title: result.title,
                matched: 0,
                chunkCount: result.chunk_count,
            });
        }
        articles.get(result.title).matched += 1;
    }

    for (const article of articles.values()) {
        const card = document.createElement("div");
        card.className = "source-card";

        const row = document.createElement("div");
        row.className = "article-row";

        const button = document.createElement("button");
        button.type = "button";
        button.className = "article-button";
        button.textContent = article.title;
        button.addEventListener("click", () => openArticle(article.title));

        const count = document.createElement("span");
        count.className = "meta";
        count.textContent = `Matched ${article.matched} / Total ${article.chunkCount} chunks`;

        row.append(button, count);
        card.appendChild(row);
        ui.details.appendChild(card);
    }
}

function renderMetrics(metrics) {
    ui.metrics.replaceChildren();

    const values = [
        ["Model", metrics.model],
        ["Search mode", metrics.search_mode],
        ["Prompt mode", metrics.prompt_mode],
        ["Thinking", metrics.think],
        ["Search time", `${metrics.search_seconds.toFixed(3)} s`],
        ["Generation time", `${metrics.generation_seconds.toFixed(3)} s`],
        ["Total time", `${metrics.total_seconds.toFixed(3)} s`],
        ["Context chars", `${metrics.context_chars_used.toLocaleString()} chars`],
        ["Search results", `${metrics.result_count} chunks`],
        ["num_ctx", metrics.num_ctx],
        ["num_predict", metrics.num_predict],
        ["temperature", metrics.temperature],
        ["Input tokens", metrics.prompt_eval_count],
        ["Output tokens", metrics.eval_count],
        [
            "Speed",
            metrics.eval_tokens_per_second == null
                ? null
                : `${metrics.eval_tokens_per_second.toFixed(2)} tok/s`,
        ],
        [
            "Thinking chars",
            metrics.thinking_chars == null
                ? null
                : `${metrics.thinking_chars.toLocaleString()} chars`,
        ],
        ["Done reason", metrics.done_reason],
    ];

    for (const [name, value] of values) {
        if (value !== null && value !== undefined && value !== "") {
            ui.metrics.appendChild(createMetric(name, String(value)));
        }
    }
}

function buildAskPayload() {
    return {
        question: ui.question.value.trim(),
        model: ui.model.value,
        top_k: Number(ui.topk.value),
        search_mode: ui.searchMode.value,
        think: ui.think.value,
        context_chars: Number(ui.contextChars.value),
        num_ctx: Number(ui.numCtx.value),
        num_predict: Number(ui.numPredict.value),
        temperature: Number(ui.temperature.value),
    };
}

async function ask() {
    const payload = buildAskPayload();

    if (!payload.question) {
        ui.question.focus();
        return;
    }
    if (!payload.model) {
        ui.status.textContent = "Please select a model.";
        return;
    }

    setBusy(true, "Searching and generating...");
    ui.resultPanel.classList.add("hidden");
    ui.errorPanel.classList.add("hidden");

    try {
        const data = await requestJson("/api/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        ui.answer.textContent = data.answer;
        renderArticles(data.results);
        renderMetrics(data.metrics);
        ui.resultPanel.classList.remove("hidden");
        ui.status.textContent = "Completed";
    } catch (error) {
        showError(error);
        ui.status.textContent = "Failed";
    } finally {
        ui.ask.disabled = false;
    }
}

async function quitApplication() {
    if (!confirm("Terminate Wikipedia RAG?")) {
        return;
    }

    ui.quit.disabled = true;
    setBusy(true, "Terminating...");

    try {
        await requestJson("/api/shutdown", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
        });
    } finally {
        document.body.innerHTML = `
            <main>
                <section class="panel">
                    <h2>Wikipedia RAG has been terminated.</h2>
                    <p>You can close this tab.</p>
                </section>
            </main>
        `;
    }
}

async function initialize() {
    try {
        await Promise.all([loadConfig(), loadModels()]);
    } catch (error) {
        showError(error);
    }
}

ui.ask.addEventListener("click", ask);
ui.quit.addEventListener("click", quitApplication);
ui.question.addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.key === "Enter") {
        ask();
    }
});

document.addEventListener("DOMContentLoaded", initialize);
