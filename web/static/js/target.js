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
    btn.innerText = rawContent.classList.contains('show') ? 'Ascunde Transcrierea' : 'Vezi Transcrierea Audio';
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
            const analysis = log.analysis || {};
            const scores = analysis.scores || {};
            const tags = analysis.tags || [];

            // Suportăm și vechiul format și noul format dur
            const exec = scores.execution !== undefined ? scores.execution : (scores.productivity || '-');
            const fulfill = scores.fulfillment !== undefined ? scores.fulfillment : (scores.happiness || '-');
            const mental = scores.mental_load !== undefined ? scores.mental_load : (scores.burnout || '-');
            const dopa = scores.dopamine_control !== undefined ? scores.dopamine_control : (scores.anger || '-');

            // Selectăm textul corect (log combinat sau log singular vechi)
            const textToShow = log.combined_text || log.raw_text || "Fără text brut disponibil.";

            const card = document.createElement('div');
            card.className = 'log-card';

            let tagsHtml = tags.map(t => `<span class="tag">${t}</span>`).join('');
            
            card.innerHTML = `
                <div class="log-top">
                    <div class="log-time">${log.type === 'daily_summary' ? 'Sinteza Zilei (Nightly Batch)' : log.display_time}</div>
                </div>
                <div class="log-summary">${analysis.short_summary || 'Fără rezumat'}</div>
                
                <div class="scores">
                    <div class="score-badge">Execuție/Muncă <span>${exec}</span></div>
                    <div class="score-badge">Împlinire Sufletească <span>${fulfill}</span></div>
                    <div class="score-badge">Încărcare Mentală <span>${mental}</span></div>
                    <div class="score-badge">Control Dopamină <span>${dopa}</span></div>
                </div>

                <div class="tags">${tagsHtml}</div>
                
                <button class="raw-btn" onclick="toggleRaw(this)">Vezi Transcrierea Completă</button>
                <div class="raw-content">
                    <b>Ce ai spus:</b><br>
                    <p style="white-space: pre-wrap;">${textToShow}</p>
                    <br><b>The Judge Feedback:</b><br>
                    <p>${analysis.judge_feedback}</p>
                </div>
            `;
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
            const analysis = log.analysis || {};
            const scores = analysis.scores || {};
            
            const searchableText = `
                ${log.combined_text || ''} ${log.raw_text || ''} 
                ${analysis.short_summary} ${analysis.judge_feedback}
                ${(analysis.tags || []).join(" ")}
                executie ${scores.execution} implinire ${scores.fulfillment}
            `.toLowerCase();

            return searchableText.includes(query);
        });

        if (matchingLogs.length > 0) {
            filteredDays.push({ date: dayObj.date, logs: matchingLogs });
        }
    });
    renderLogs(filteredDays);
});