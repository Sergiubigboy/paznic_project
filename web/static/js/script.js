let allData = [];

fetch('/api/logs')
    .then(response => response.json())
    .then(data => {
        allData = data;
        renderLogs(allData);
    });

function toggleRaw(btn) {
    const rawContent = btn.nextElementSibling;
    rawContent.classList.toggle('show');
    btn.innerText = rawContent.classList.contains('show') ? 'Ascunde Textul' : 'Vezi Textul Complet';
}

function renderLogs(daysArray) {
    const container = document.getElementById('logsContainer');
    container.innerHTML = '';

    if (daysArray.length === 0) {
        document.getElementById('noResults').style.display = 'block';
        return;
    } else {
        document.getElementById('noResults').style.display = 'none';
    }

    daysArray.forEach(dayObj => {
        const dayGroup = document.createElement('div');
        dayGroup.className = 'day-group';
        
        const dateStr = new Date(dayObj.date).toLocaleDateString('ro-RO', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
        dayGroup.innerHTML = `<div class="day-header">${dateStr}</div>`;

        dayObj.logs.forEach(log => {
            const card = document.createElement('div');
            card.className = 'log-card';

            if (log.type === 'daily_summary') {
                // Afișăm CARDUL DE SINTEZĂ
                const analysis = log.analysis || {};
                const scores = analysis.scores || {};
                const tags = analysis.tags || [];
                let tagsHtml = tags.map(t => `<span class="tag">${t}</span>`).join('');
                
                card.style.borderLeft = "4px solid var(--primary)"; // Evidențiem vizual că e rezumatul
                
                card.innerHTML = `
                    <div class="log-top">
                        <div class="log-time">⚖️ Sinteza Zilei (The Judge)</div>
                    </div>
                    <div class="log-summary">${analysis.short_summary || 'Fără rezumat'}</div>
                    
                    <div class="scores">
                        <div class="score-badge">Execuție <span>${scores.execution || '-'}</span></div>
                        <div class="score-badge">Împlinire <span>${scores.fulfillment || '-'}</span></div>
                        <div class="score-badge">Stres <span>${scores.mental_load || '-'}</span></div>
                        <div class="score-badge">Dopamină <span>${scores.dopamine_control || '-'}</span></div>
                    </div>

                    <div class="tags">${tagsHtml}</div>
                    
                    <div style="margin-top: 15px; padding: 15px; background-color: rgba(157, 78, 221, 0.05); border-radius: 8px;">
                        <b style="color: var(--primary);">Feedback The Judge:</b><br>
                        <p style="margin-top: 5px; margin-bottom: 0;">${analysis.judge_feedback}</p>
                    </div>
                `;
            } else {
                // Afișăm CARDURILE RAW (Înregistrările de peste zi)
                card.innerHTML = `
                    <div class="log-top">
                        <div class="log-time">🎤 Înregistrare de la ${log.display_time}</div>
                    </div>
                    <p style="white-space: pre-wrap; color: var(--text-muted); font-style: italic;">"${log.raw_text}"</p>
                `;
            }
            
            dayGroup.appendChild(card);
        });

        container.appendChild(dayGroup);
    });
}

document.getElementById('searchInput').addEventListener('input', function(e) {
    const query = e.target.value.toLowerCase().trim();
    if (!query) { renderLogs(allData); return; }

    const filteredDays = [];
    allData.forEach(dayObj => {
        const matchingLogs = dayObj.logs.filter(log => {
            if (log.type === 'daily_summary') {
                const analysis = log.analysis || {};
                const scores = analysis.scores || {};
                const searchableText = `
                    ${log.combined_text || ''} 
                    ${analysis.short_summary || ''} ${analysis.judge_feedback || ''}
                    ${(analysis.tags || []).join(" ")}
                    executie ${scores.execution} implinire ${scores.fulfillment}
                `.toLowerCase();
                return searchableText.includes(query);
            } else {
                return log.raw_text && log.raw_text.toLowerCase().includes(query);
            }
        });

        if (matchingLogs.length > 0) {
            filteredDays.push({ date: dayObj.date, logs: matchingLogs });
        }
    });
    renderLogs(filteredDays);
});

