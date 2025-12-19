
// type effect on papers search input
document.addEventListener('DOMContentLoaded', function () {
    const inputField = document.querySelector('input[name="query"]')
    const suggestions = ['Search for space exploration research papers', 'Astrobiology', 'Microgravity', 'Space habitats', 'Lunar missions', 'Exoplanet exploration']
    let index = 0

    function typeEffect() {
        const suggestion = suggestions[index]
        let suggestionIndex = 0

        let typing = setInterval(function () {
            if (suggestionIndex <= suggestion.length) {
                inputField.placeholder = suggestion.substring(0, suggestionIndex)
                suggestionIndex++
            } else {
                clearInterval(typing)
                setTimeout(function () {
                    index = (index + 1) % suggestions.length // Reset index to 0 if it reaches the end
                    typeEffect()
                }, 1000) // Delay before showing the next suggestion
            }
        }, 80) // Adjust the delay here for typing speed
    }

    typeEffect()
})