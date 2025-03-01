from flask import Flask, request, render_template
import requests
from bs4 import BeautifulSoup
import unicodedata
import re
from fuzzywuzzy import fuzz
import logging

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

SEARCH_URL = "https://www.svenskfilmdatabas.se/wp-admin/admin-ajax.php?action=quick_search&language=sv"

def normalize_title(title):
    """Normalisera titlar till små bokstäver och ta bort accenter/specialtecken."""
    title = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode('utf-8')
    title = re.sub(r'[^a-zA-Z0-9\s]', '', title)
    return title.lower().strip()

def search_movie(title, year=None):
    params = {"s": title}
    response = requests.get(SEARCH_URL, params=params)
    
    if response.status_code != 200:
        logging.error("Misslyckades med att hämta sökresultat.")
        return None, None

    # Ta bort BOM om den finns
    response_text = response.text.lstrip("\ufeff")
    logging.debug(f"Sökrespons: {response_text}")

    # Försök parsa responsen som JSON
    try:
        search_results = eval(response_text)
    except Exception as e:
        logging.error(f"Fel vid parsning av sökresultat: {e}")
        return None, None

    normalized_input_title = normalize_title(title)
    logging.debug(f"Normaliserad inmatad titel: {normalized_input_title}")

    best_match = None
    best_year = None
    best_score = 0
    smallest_year_diff = float('inf')  # Oändligt stort initialt för att hitta minsta skillnad

    # Konvertera användarens år till int om det finns
    input_year = None if not year else int(year) if year.isdigit() else None

    # Sök efter filmer i responsen
    for group in search_results:
        if "items" in group:
            for item in group["items"]:
                if "type=film" in item["url"]:
                    movie_title_with_year = item["title"]
                    normalized_movie_title = normalize_title(movie_title_with_year)
                    logging.debug(f"Kollar titel: {movie_title_with_year} (Normaliserad: {normalized_movie_title})")

                    # Uteslut trailers
                    if "trailer" in normalized_movie_title:
                        continue

                    # Hämta året från titeln
                    found_year = None
                    if "(" in movie_title_with_year and ")" in movie_title_with_year:
                        found_year_str = movie_title_with_year.split("(")[-1].split(")")[0]
                        if found_year_str.isdigit():
                            found_year = int(found_year_str)

                    # Beräkna likhetsscore
                    score = fuzz.ratio(normalized_input_title, normalized_movie_title)
                    logging.debug(f"Likhetsscore: {score} för {movie_title_with_year}")

                    # Om året är angivet, prioritera baserat på närhet till input_year
                    if input_year and found_year:
                        year_diff = abs(input_year - found_year)
                        # Om score är tillräckligt högt och året är närmare, uppdatera bästa match
                        if score >= 80:  # Tröskel för att säkerställa titelmatchning
                            if year_diff < smallest_year_diff or (year_diff == smallest_year_diff and score > best_score):
                                smallest_year_diff = year_diff
                                best_score = score
                                best_match = "https://www.svenskfilmdatabas.se" + item["url"]
                                best_year = found_year
                    # Om inget år är angivet, använd bara likhetsscore
                    elif score > best_score:
                        best_score = score
                        best_match = "https://www.svenskfilmdatabas.se" + item["url"]
                        best_year = found_year

    if best_match:
        logging.debug(f"Bästa matchning: {best_match} (År: {best_year})")
    else:
        logging.warning("Ingen matchande film hittades.")

    return best_match, best_year

def check_dcp_availability(movie_url):
    response = requests.get(movie_url)
    if response.status_code != 200:
        return False

    soup = BeautifulSoup(response.text, "html.parser")

    # Kolla tabeller, divs, paragrafer och hela sidans text
    for element in soup.find_all(["table", "div", "p"]) + [soup]:
        if "DCP" in element.get_text(separator=" ", strip=True).upper():
            return True
    return False

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        movie_title = request.form.get('movie_title')
        release_year = request.form.get('release_year')

        # Sök film och ta hänsyn till året
        movie_url, found_year = search_movie(movie_title, year=release_year if release_year else None)

        if movie_url:
            dcp_available = check_dcp_availability(movie_url)
            year_display = found_year if found_year else "Okänt år"
            if dcp_available:
                result = f"DCP är tillgängligt för '{movie_title}' ({year_display})."
            else:
                result = f"DCP nämns inte för '{movie_title}' ({year_display})."
        else:
            result = "Kunde inte hitta filmen."
            movie_url = None

        return render_template('index.html', result=result, movie_url=movie_url)

    return render_template('index.html', result=None, movie_url=None)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)