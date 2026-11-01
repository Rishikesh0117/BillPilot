from django import forms

MAX_UPLOAD_MB = 10


class UploadForm(forms.Form):
    file = forms.FileField(label="Invoice or receipt (PDF)")

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Please upload a PDF file.")
        if f.size > MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(f"File is too large (max {MAX_UPLOAD_MB} MB).")
        head = f.read(5)
        f.seek(0)
        if head != b"%PDF-":
            raise forms.ValidationError("That file does not look like a valid PDF.")
        return f