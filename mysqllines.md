from mysql.connector import connect
c = connect(host = "localhost", user = "serv-rem-dev", password = "password", database = "service_reminders_app")
curs = c.cursor()
