from flask_cors import CORS

cors = CORS(
    expose_headers=[
        "X-WJGL-MinerU-Key",
        "X-WJGL-Success",
        "X-WJGL-Html-Bytes",
        "X-WJGL-Preview-Mode",
    ],
)

def init_extensions(app):
    cors.init_app(app)
    return app
