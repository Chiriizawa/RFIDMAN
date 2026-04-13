from RFID import create_app

app = create_app()

if __name__ == '__main__':
    print(app.url_map)  
    app.run(debug=True, use_reloader=False)