// Animated hero chat widget for Takopi docs
(function() {
  // Real session data with timing (ms from start)
  const TIME_SCALE = 0.5; // 2x speed
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

  async function animateChat(container) {
    const messages = container.querySelector('.chat-messages');
    messages.innerHTML = '';

    // User message appears
    await new Promise(r => setTimeout(r, 800 * TIME_SCALE));
    const userMsg = document.createElement('div');
    userMsg.className = 'msg msg-user';
    userMsg.textContent = 'what does this project do?';
    messages.appendChild(userMsg);

    // Bot starts responding
    await new Promise(r => setTimeout(r, 600 * TIME_SCALE));
    const botMsg = document.createElement('div');
    botMsg.className = 'msg msg-bot';
    botMsg.innerHTML = '<div class="status">starting · codex · 0s</div><div class="tools"></div>';
    messages.appendChild(botMsg);

    const statusEl = botMsg.querySelector('.status');
    const toolsDiv = botMsg.querySelector('.tools');
    const startTime = Date.now();
    const allTools = [];

    // Timer updates every second (real time)
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

      // Mark previous tool as done
      const prevRunning = toolsDiv.querySelector('.running');
      if (prevRunning) prevRunning.classList.remove('running');

      // Add event line
      const toolEl = document.createElement('div');
      toolEl.className = event.cmd ? 'tool cmd running' : 'tool running';
      toolEl.textContent = event.thinking || event.cmd;
      allTools.push(toolEl);
      toolsDiv.appendChild(toolEl);

      // Keep only last MAX_VISIBLE
      if (allTools.length > MAX_VISIBLE) {
        const old = allTools.shift();
        old.remove();
      }
    }

    // Mark last tool as done
    const lastRunning = toolsDiv.querySelector('.running');
    if (lastRunning) lastRunning.classList.remove('running');

    // Wait for answer
    const remaining = ANSWER_TIME * TIME_SCALE - (Date.now() - startTime);
    if (remaining > 0) await new Promise(r => setTimeout(r, remaining));

    // Wait for done
    const doneRemaining = DONE_TIME * TIME_SCALE - (Date.now() - startTime);
    if (doneRemaining > 0) await new Promise(r => setTimeout(r, doneRemaining));

    clearInterval(timerInterval);
    const finalElapsed = Math.floor((Date.now() - startTime) / 1000);

    // Show done state with answer
    botMsg.innerHTML = `
      <div class="status">done · codex · ${finalElapsed}s · step ${step}</div>
      <div class="answer">${ANSWER}</div>
    `;
  }

  // Initialize when DOM is ready
  function init() {
    const containers = document.querySelectorAll('.hero-chat');
    containers.forEach(container => {
      animateChat(container);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
