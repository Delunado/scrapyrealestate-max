from flask import Flask, render_template, request

from scrapyrealestate.atomic_files import atomic_write_json
from scrapyrealestate.runtime import get_runtime_paths

app = Flask(__name__, template_folder='templates')
runtime_paths = get_runtime_paths()


@app.route('/')
@app.route('/home')
def home():
    return render_template("index.html")


@app.route('/data', methods=['POST', 'GET'])
def result():
    dict_form = request.form.to_dict()
    if dict_form != {}:
        # Cada portal admite varias URLs: las recogemos como lista.
        for portal in ("url_idealista", "url_pisoscom", "url_fotocasa", "url_habitaclia", "url_yaencontre"):
            dict_form[portal] = request.form.getlist(portal)
        atomic_write_json(runtime_paths.config_file, dict_form)
        return render_template("info.html")

    return render_template("index.html")


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
