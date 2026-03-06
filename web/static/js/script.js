let allData = [];

// Încărcăm datele de la server la pornire
fetch('/api/logs')
    .then(response => response.json())
    .then(data => {
        allData = data;
        renderLogs(allData);
    });

// Funcție pentru a ascunde/arăta textul raw
function toggleRaw(btn) {
    const rawContent = btn.nextElementSibling;
    rawContent.classList.toggle('show');
    btn.innerText = rawContent.classList.contains('show') ? 'Ascunde Transcrierea' : 'Vezi Transcrierea Audio';
}

// Randează logurile pe ecran
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
        
        // Formatează data frumos
        const dateStr = new Date(dayObj.date).toLocaleDateString('ro-RO', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
        
        dayGroup.innerHTML = `<div class="day-header">${dateStr}</div>`;

        dayObj.logs.forEach(log => {
            const analysis = log.analysis || {};
            const scores = analysis.scores || {};
            const tags = analysis.tags || [];

            const card = document.createElement('div');
            card.className = 'log-card';

            let tagsHtml = tags.map(t => `<span class="tag">${t}</span>`).join('');
            
            card.innerHTML = `
                <div class="log-top">
                    <div class="log-time">${log.display_time}</div>
                </div>
                <div class="log-summary">${analysis.short_summary || 'Fără rezumat'}</div>
                
                <div class="scores">
                    <div class="score-badge">Productivitate <span>${scores.productivity || '-'}</span></div>
                    <div class="score-badge">Fericire <span>${scores.happiness || '-'}</span></div>
                    <div class="score-badge">Stres <span>${scores.burnout || '-'}</span></div>
                    <div class="score-badge">Furie <span>${scores.anger || '-'}</span></div>
                </div>

                <div class="tags">${tagsHtml}</div>
                
                <button class="raw-btn" onclick="toggleRaw(this)">Vezi Transcrierea Audio</button>
                <div class="raw-content">"${log.raw_text}" <br><br><b>Feedback AI:</b><br>${analysis.judge_feedback}</div>
            `;
            dayGroup.appendChild(card);
        });

        container.appendChild(dayGroup);
    });
}

// Filtru Live (Search)
document.getElementById('searchInput').addEventListener('input', function(e) {
    const query = e.target.value.toLowerCase().trim();
    
    if (!query) {
        renderLogs(allData);
        return;
    }

    const filteredDays = [];

    allData.forEach(dayObj => {
        const matchingLogs = dayObj.logs.filter(log => {
            const analysis = log.analysis || {};
            const scores = analysis.scores || {};
            
            // Creează un șir cu TOATE datele logului pentru a căuta în ele
            const searchableText = `
                ${log.raw_text} 
                ${analysis.short_summary} 
                ${analysis.judge_feedback}
                ${(analysis.tags || []).join(" ")}
                productivitate ${scores.productivity}
                fericire ${scores.happiness}
                burnout ${scores.burnout}
                stres ${scores.burnout}
                furie ${scores.anger}
            `.toLowerCase();

            return searchableText.includes(query);
        });

        if (matchingLogs.length > 0) {
            filteredDays.push({
                date: dayObj.date,
                logs: matchingLogs
            });
        }
    });

    renderLogs(filteredDays);
});