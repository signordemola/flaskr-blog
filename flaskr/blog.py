import math
import os
import uuid

import markdown
from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.exceptions import abort
from werkzeug.utils import secure_filename

from flaskr.auth import login_required
from flaskr.db import get_db

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_FILE_SIZE_MB = 5

PER_PAGE = 5


bp = Blueprint("blog", __name__)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def process_tags(post_id, tags_string):
    db = get_db()

    db.execute("DELETE FROM post_tag WHERE post_id = ?", (post_id,))

    if not tags_string:
        return

    tag_names = [tag.strip().lower() for tag in tags_string.split(",") if tag.strip()]

    for tag_name in set(tag_names):
        tag_id_row = db.execute(
            "SELECT id FROM tag WHERE name = ?", (tag_name,)
        ).fetchone()

        if tag_id_row:
            tag_id = tag_id_row["id"]
        else:
            cursor = db.execute("INSERT INTO tag (name) VALUES (?)", (tag_name,))
            tag_id = cursor.lastrowid

        db.execute(
            "INSERT INTO post_tag (post_id, tag_id) VALUES (?, ?)", (post_id, tag_id)
        )

    db.commit()


def get_post_tags(post_id):
    db = get_db()
    tags = db.execute(
        "SELECT t.name FROM tag t JOIN post_tag pt ON t.id = pt.tag_id WHERE pt.post_id = ?",
        (post_id,),
    ).fetchall()
    return [tag["name"] for tag in tags]


@bp.route("/")
def index():
    db = get_db()
    page = request.args.get("page", 1, type=int)
    offset = (page - 1) * PER_PAGE

    # Refactored SQL query to correctly count likes and group tags
    posts = db.execute(
        """
        SELECT
            p.id, p.title, p.body, p.created, p.author_id, u.username, p.image_url,
            COALESCE(l.like_count, 0) AS like_count,
            CASE WHEN EXISTS (SELECT 1 FROM post_like WHERE post_id = p.id AND user_id = ?) THEN 1 ELSE 0 END AS user_has_liked,
            t.tags_string AS tags
        FROM post p
        JOIN user u ON p.author_id = u.id
        LEFT JOIN (
            SELECT post_id, COUNT(user_id) AS like_count
            FROM post_like
            GROUP BY post_id
        ) l ON p.id = l.post_id
        LEFT JOIN (
            SELECT pt.post_id, GROUP_CONCAT(t.name, ',') AS tags_string
            FROM post_tag pt
            JOIN tag t ON pt.tag_id = t.id
            GROUP BY pt.post_id
        ) t ON p.id = t.post_id
        ORDER BY p.created DESC LIMIT ? OFFSET ?
        """,
        (g.user["id"] if g.user else None, PER_PAGE, offset),
    ).fetchall()

    processed_posts = []
    for post in posts:
        post_dict = dict(post)
        post_dict["body"] = markdown.markdown(post_dict["body"])
        # Ensure tags is a list, even if no tags are present (tags_string would be None)
        post_dict["tags"] = post_dict["tags"].split(",") if post_dict["tags"] else []
        processed_posts.append(post_dict)

    total_posts = db.execute("SELECT COUNT(id) FROM post").fetchone()[0]
    total_pages = math.ceil(total_posts / PER_PAGE)

    return render_template(
        "blog/index.html", posts=processed_posts, page=page, total_pages=total_pages
    )


@bp.route("/create", methods=("GET", "POST"))
@login_required
def create():
    if request.method == "POST":
        title = request.form["title"]
        body = request.form["body"]
        tags_string = request.form.get("tags", "")
        image_file = request.files.get("image")
        error = None
        image_url = None

        if not title:
            error = "Title is required."

        if error is None and image_file:
            if image_file.filename == "":
                image_file = None
            elif not allowed_file(image_file.filename):
                error = "Invalid file type. Allowed extensions are: " + ", ".join(
                    ALLOWED_EXTENSIONS
                )
            elif image_file.content_length > MAX_FILE_SIZE_MB * 1024 * 1024:
                error = f"File size exceeds {MAX_FILE_SIZE_MB}MB."

        if error is not None:
            flash(error)
        else:
            db = get_db()
            post_id = None

            if image_file:
                filename = secure_filename(image_file.filename)
                unique_filename = str(uuid.uuid4()) + "_" + filename

                upload_folder = current_app.config.get("UPLOAD_FOLDER")
                if not upload_folder:
                    upload_folder = os.path.join(current_app.root_path, "uploads")
                    if not os.path.exists(upload_folder):
                        os.makedirs(upload_folder)

                file_path = os.path.join(upload_folder, unique_filename)

                try:
                    image_file.save(file_path)
                    image_url = url_for("static", filename="uploads/" + unique_filename)
                except Exception as e:
                    flash(f"Error saving image: {e}")
                    image_url = None

            cursor = db.execute(
                "INSERT INTO post (title, body, author_id, image_url) VALUES (?, ?, ?, ?)",
                (title, body, g.user["id"], image_url),
            )
            db.commit()
            post_id = cursor.lastrowid

            if post_id:
                process_tags(post_id, tags_string)

            flash("Post created successfully!")
            return redirect(url_for("blog.index"))

    return render_template("blog/create.html")


def get_post(id, check_author=True):
    db = get_db()

    # Refactored SQL query for single post detail
    post = db.execute(
        """
        SELECT
            p.id, p.title, p.body, p.created, p.author_id, u.username, p.image_url,
            COALESCE(l.like_count, 0) AS like_count,
            CASE WHEN EXISTS (SELECT 1 FROM post_like WHERE post_id = p.id AND user_id = ?) THEN 1 ELSE 0 END AS user_has_liked,
            t.tags_string AS tags
        FROM post p
        JOIN user u ON p.author_id = u.id
        LEFT JOIN (
            SELECT post_id, COUNT(user_id) AS like_count
            FROM post_like
            GROUP BY post_id
        ) l ON p.id = l.post_id
        LEFT JOIN (
            SELECT pt.post_id, GROUP_CONCAT(t.name, ',') AS tags_string
            FROM post_tag pt
            JOIN tag t ON pt.tag_id = t.id
            GROUP BY pt.post_id
        ) t ON p.id = t.post_id
        WHERE p.id = ?
        """,
        (g.user["id"] if g.user else None, id),
    ).fetchone()

    if post is None:
        abort(404, f"Post id {id} doesn't exist.")

    if check_author and post["author_id"] != g.user["id"]:
        abort(403)

    post_dict = dict(post)
    post_dict["body"] = markdown.markdown(post_dict["body"])
    # Ensure tags is a list, even if no tags are present (tags_string would be None)
    post_dict["tags"] = post_dict["tags"].split(",") if post_dict["tags"] else []

    return post_dict


@bp.route("/<int:id>/update", methods=("GET", "POST"))
@login_required
def update(id):
    post = get_post(id)

    db = get_db()
    raw_post_row = db.execute("SELECT body FROM post WHERE id = ?", (id,)).fetchone()
    raw_post_body = raw_post_row["body"] if raw_post_row else ""

    current_tags = get_post_tags(id)
    current_tags_string = ", ".join(current_tags)

    if request.method == "POST":
        title = request.form["title"]
        body = request.form["body"]
        tags_string = request.form.get("tags", "")
        image_file = request.files.get("image")
        error = None
        image_url = post["image_url"]

        if not title:
            error = "Title is required."

        if error is None and image_file:
            if image_file.filename == "":
                pass
            elif not allowed_file(image_file.filename):
                error = "Invalid file type. Allowed extensions are: " + ", ".join(
                    ALLOWED_EXTENSIONS
                )
            elif image_file.content_length > MAX_FILE_SIZE_MB * 1024 * 1024:
                error = f"File size exceeds {MAX_FILE_SIZE_MB}MB."
            else:
                if post["image_url"]:
                    old_filename = os.path.basename(post["image_url"])
                    upload_folder = current_app.config.get("UPLOAD_FOLDER")
                    if upload_folder:
                        old_file_path = os.path.join(upload_folder, old_filename)
                        if os.path.exists(old_file_path):
                            os.remove(old_file_path)

                filename = secure_filename(image_file.filename)
                unique_filename = str(uuid.uuid4()) + "_" + filename
                upload_folder = current_app.config.get("UPLOAD_FOLDER")
                if not upload_folder:
                    upload_folder = os.path.join(current_app.root_path, "uploads")
                    if not os.path.exists(upload_folder):
                        os.makedirs(upload_folder)

                file_path = os.path.join(upload_folder, unique_filename)
                try:
                    image_file.save(file_path)
                    image_url = url_for("static", filename="uploads/" + unique_filename)
                except Exception as e:
                    flash(f"Error saving new image: {e}")
                    image_url = post["image_url"]

        if error is not None:
            flash(error)
        else:
            db = get_db()
            db.execute(
                "UPDATE post SET title = ?, body = ?, image_url = ? WHERE id = ?",
                (title, body, image_url, id),
            )
            db.commit()

            process_tags(id, tags_string)

            flash("Post updated successfully!")
            return redirect(url_for("blog.index"))

    return render_template(
        "blog/update.html",
        post=post,
        raw_post_body=raw_post_body,
        current_tags_string=current_tags_string,
    )


@bp.route("/<int:id>/delete", methods=("POST",))
@login_required
def delete(id):
    post = get_post(id, check_author=False)
    db = get_db()
    db.execute("DELETE FROM post WHERE id = ?", (id,))
    db.commit()

    if post["image_url"]:
        filename_from_url = os.path.basename(post["image_url"])
        upload_folder = current_app.config.get("UPLOAD_FOLDER")
        if upload_folder:
            file_path = os.path.join(upload_folder, filename_from_url)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    print(f"Deleted image file: {file_path}")
                except OSError as e:
                    print(f"Error deleting image file {file_path}: {e}")

    flash("Post deleted successfully!")
    return redirect(url_for("blog.index"))


@bp.route("/<int:post_id>/like", methods=("POST",))
@login_required
def like_post(post_id):
    db = get_db()
    user_id = g.user["id"]

    like_exists = db.execute(
        "SELECT 1 FROM post_like WHERE user_id = ? AND post_id = ?",
        (user_id, post_id),
    ).fetchone()

    if like_exists:
        db.execute(
            "DELETE FROM post_like WHERE user_id = ? AND post_id = ?",
            (user_id, post_id),
        )
        flash("Post unliked.")
    else:
        db.execute(
            "INSERT INTO post_like (user_id, post_id) VALUES (?, ?)",
            (user_id, post_id),
        )
        flash("Post liked!")

    db.commit()

    referrer = request.referrer
    if referrer and url_for("blog.post_detail", id=post_id) in referrer:
        return redirect(url_for("blog.post_detail", id=post_id))
    return redirect(url_for("blog.index"))


@bp.route("/<int:id>/detail", methods=("GET", "POST"))
def post_detail(id):
    post = get_post(id, check_author=False)

    db = get_db()
    comments = db.execute(
        "SELECT c.id, c.body, c.created, u.username"
        " FROM comment c JOIN user u ON c.author_id = u.id"
        " WHERE c.post_id = ?"
        " ORDER BY c.created ASC",
        (id,),
    ).fetchall()

    if request.method == "POST":
        if not g.user:
            flash("You must be logged in to comment.")
            return redirect(url_for("auth.login"))

        comment_body = request.form["comment_body"]
        error = None

        if not comment_body:
            error = "Comment cannot be empty."

        if error is not None:
            flash(error)
        else:
            db.execute(
                "INSERT INTO comment (post_id, author_id, body) VALUES (?, ?, ?)",
                (id, g.user["id"], comment_body),
            )
            db.commit()
            flash("Comment added successfully!")
            return redirect(url_for("blog.post_detail", id=id))

    return render_template("blog/post_detail.html", post=post, comments=comments)
