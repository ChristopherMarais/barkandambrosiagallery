document.addEventListener('DOMContentLoaded', function () {
    var countdownElements = document.querySelectorAll('.countdown');
    countdownElements.forEach(function (element) {
        var launchDate = element.dataset.launchDate;
        var windowEnd = element.dataset.windowEnd;
        updateCountdown(element, launchDate, windowEnd);
    });
});

function updateCountdown(element, launchDate, windowEnd) {
    var launchTimeUTC = Date.parse(launchDate);
    var windowEndUTC = Date.parse(windowEnd);

    if (isNaN(launchTimeUTC) || isNaN(windowEndUTC)) {
        console.error("Data de lançamento inválida: ", launchDate);
        element.innerHTML = "Data inválida!";
        return;
    }

    var x = setInterval(function () {
        var now = new Date().getTime();  // Horário atual do usuário
        var distanceToStart = launchTimeUTC - now;  // Tempo até o início do lançamento
        var distanceToEnd = windowEndUTC - now;  // Tempo até o fim da janela de lançamento

        if (distanceToEnd < 0) {
            clearInterval(x);
            element.innerHTML = 'Launch Finished';  // Lançamento finalizado
        } else if (distanceToStart < 0 && distanceToEnd > 0) {
            element.innerHTML = 'Launching!';  // Lançamento em andamento
        } else {
            var days = Math.floor(distanceToStart / (1000 * 60 * 60 * 24));
            var hours = Math.floor((distanceToStart % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
            var minutes = Math.floor((distanceToStart % (1000 * 60 * 60)) / (1000 * 60));
            var seconds = Math.floor((distanceToStart % (1000 * 60)) / 1000);

            element.innerHTML = days + 'd ' + hours + 'h ' + minutes + 'm ' + seconds + 's ';
        }
    }, 1000);

    var userLaunchDateElements = document.querySelectorAll('[data-user-window-start]');
    userLaunchDateElements.forEach(function (element) {
        var launchDate = element.dataset.userWindowStart;
        if (launchDate) {
            var localLaunchTime = new Date(launchDate).toLocaleString('en-US', {
                month: 'long',   // Nome completo do mês
                day: 'numeric',  // Dia do mês
                year: 'numeric', // Ano completo
                hour: 'numeric', // Hora no formato 12 horas
                minute: 'numeric', // Minutos
                hour12: true    // Para usar AM/PM
            });

            element.textContent = localLaunchTime; // Atualiza o conteúdo do HTML
        }
    });
}