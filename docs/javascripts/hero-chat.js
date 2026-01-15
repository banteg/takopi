// Animated hero chat + terminal widget for Takopi docs
(function() {
  const TIME_SCALE = 0.5; // 2x speed
  const RESUME_ID = '019bb498';
  const RESUME_CMD = `codex resume ${RESUME_ID}`;

  const EVENTS = [
    { time: 2515, thinking: "Listing files for inspection" },
    { time: 2892, cmd: "ls" },
    { time: 4755, thinking: "Inspecting readme" },
    { time: 4982, cmd: "cat readme.md" },
    { time: 7217, thinking: "Scanning source structure" },
    { time: 7642, cmd: "ls src" },
    { time: 9024, cmd: "ls src/takopi" },
    { time: 10927, thinking: "Exploring Telegram integration" },
    { time: 11213, cmd: "rg telegram src/takopi" },
    { time: 14884, thinking: "Planning deeper codebase inspection" },
    { time: 15210, cmd: "rg scripts pyproject.toml" },
    { time: 16796, cmd: "cat pyproject.toml" },
    { time: 21565, thinking: "Summarizing project purpose" },
  ];

  const ANSWER_TIME = 21500;
  const DONE_TIME = 23000;
  const MAX_VISIBLE = 5;

  const ANSWER = `Takopi is a Telegram bridge for agent CLIs like Codex, Claude Code, OpenCode, and Pi. It lets you run agents from chat, stream progress back, manage multiple repos and branches, and resume sessions from either chat or terminal.`;

  const USER_QUESTION = 'what does this project do?';

  async function typeText(element, text, delay = 30) {
    for (const char of text) {
      element.textContent += char;
      await new Promise(r => setTimeout(r, delay));
    }
  }

  async function animateDemo() {
    const chat = document.querySelector('.hero-chat');
    const terminal = document.querySelector('.hero-terminal');
    if (!chat || !terminal) return;

    const messages = chat.querySelector('.chat-messages');
    const termContent = terminal.querySelector('.terminal-content');
    messages.innerHTML = '';
    termContent.innerHTML = '<div class="prompt shell"><span class="prompt-symbol">$</span> <span class="prompt-input"></span><span class="cursor">▋</span></div>';

    // User message appears
    await new Promise(r => setTimeout(r, 800 * TIME_SCALE));
    const userMsg = document.createElement('div');
    userMsg.className = 'msg msg-user';
    userMsg.textContent = USER_QUESTION;
    messages.appendChild(userMsg);

    // Bot starts responding
    await new Promise(r => setTimeout(r, 600 * TIME_SCALE));
    const botMsg = document.createElement('div');
    botMsg.className = 'msg msg-bot';
    botMsg.innerHTML = `<div class="status">starting · codex · 0s</div><div class="tools"></div><div class="resume">${RESUME_CMD}</div>`;
    messages.appendChild(botMsg);

    const statusEl = botMsg.querySelector('.status');
    const toolsDiv = botMsg.querySelector('.tools');
    const startTime = Date.now();
    const allTools = [];

    let step = 0;
    const timerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - startTime) / 1000);
      if (step === 0) {
        statusEl.textContent = `starting · codex · ${elapsed}s`;
      } else {
        statusEl.textContent = `working · codex · ${elapsed}s · step ${step}`;
      }
    }, 1000);

    // Schedule each event
    for (const event of EVENTS) {
      const wait = event.time * TIME_SCALE - (Date.now() - startTime);
      if (wait > 0) await new Promise(r => setTimeout(r, wait));

      step++;

      const elapsed = Math.floor((Date.now() - startTime) / 1000);
      statusEl.textContent = `working · codex · ${elapsed}s · step ${step}`;

      const prevRunning = toolsDiv.querySelector('.running');
      if (prevRunning) prevRunning.classList.remove('running');

      const toolEl = document.createElement('div');
      toolEl.className = event.cmd ? 'tool cmd running' : 'tool running';
      toolEl.textContent = event.thinking || event.cmd;
      allTools.push(toolEl);
      toolsDiv.appendChild(toolEl);

      if (allTools.length > MAX_VISIBLE) {
        const old = allTools.shift();
        old.remove();
      }
    }

    const lastRunning = toolsDiv.querySelector('.running');
    if (lastRunning) lastRunning.classList.remove('running');

    const remaining = ANSWER_TIME * TIME_SCALE - (Date.now() - startTime);
    if (remaining > 0) await new Promise(r => setTimeout(r, remaining));

    const doneRemaining = DONE_TIME * TIME_SCALE - (Date.now() - startTime);
    if (doneRemaining > 0) await new Promise(r => setTimeout(r, doneRemaining));

    clearInterval(timerInterval);
    const finalElapsed = Math.floor((Date.now() - startTime) / 1000);

    // Show done state with answer and resume line
    botMsg.innerHTML = `
      <div class="status">done · codex · ${finalElapsed}s · step ${step}</div>
      <div class="answer">${ANSWER}</div>
      <div class="resume">${RESUME_CMD}</div>
    `;

    // Wait, then animate terminal
    await new Promise(r => setTimeout(r, 1500));

    // Type resume command in terminal
    const promptInput = termContent.querySelector('.prompt-input');
    await typeText(promptInput, RESUME_CMD, 40);

    // Press enter
    await new Promise(r => setTimeout(r, 300));
    termContent.querySelector('.cursor').remove();
    termContent.querySelector('.prompt').classList.add('executed');

    // Show codex output
    await new Promise(r => setTimeout(r, 500));
    const output = document.createElement('div');
    output.className = 'codex-output';
    output.innerHTML = `<div class="codex-msg user">${USER_QUESTION}</div>
<div class="codex-msg assistant">${ANSWER}</div>
<div class="codex-prompt"><span class="codex-input"></span><span class="cursor">▋</span></div>`;
    termContent.appendChild(output);

    // Type a follow-up message
    await new Promise(r => setTimeout(r, 800));
    const codexInput = output.querySelector('.codex-input');
    await typeText(codexInput, 'omg takopi you are the best', 50);
  }

  function init() {
    if (document.querySelector('.hero-chat') && document.querySelector('.hero-terminal')) {
      animateDemo();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
