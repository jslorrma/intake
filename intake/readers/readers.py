import importlib.metadata

from intake import import_name
from intake.readers import datatypes
from intake.readers.utils import subclasses


class BaseReader:
    imports = set()  # allow for pip-style versions maybe
    implements = set()
    optional_imports = set()
    func = "builtins:NotImplementedError"
    concat_func = None
    output_instance = None
    url_arg = None
    storage_options = False

    def __init__(self, data):
        self.data = data

    @classmethod
    def check_imports(cls):
        """See if required packages are importable, but don't import them"""
        try:
            for package in cls.imports:
                importlib.metadata.distribution(package)
            return True
        except (ImportError, ModuleNotFoundError, NameError):
            return False

    @classmethod
    def doc(cls):
        """Doc associated with loading function"""
        func = import_name(cls.func)
        return func.__doc__

    def glimpse(self, **kwargs):
        """Minimal snapshot of the data"""
        raise NotImplementedError

    @property
    def _func(self):
        if isinstance(self.func, str):
            return import_name(self.func)
        return self.func

    def read(self, **kwargs):
        """Produce data artefact

        Output type is given by the .output_instance attribute
        """
        kw = self.data.kwargs.copy()
        kw.update(kwargs)
        if self.storage_options:
            kw["storage_options"] = self.data.storage_options
        if self.url_arg and self.url_arg not in kwargs and self.concat_func:
            filelist = self.data.filelist
            if len(filelist) > 1:
                concat = import_name(self.concat_func)
                parts = []
                for afile in filelist:
                    kw[self.url_arg] = afile
                    # TODO: can apply file columns here
                    parts.append(self.read(**kw))
                return concat(parts)
        if self.url_arg:
            kw[self.url_arg] = self.data.url

        return self._func(**kw)


class Pandas(BaseReader):
    imports = {"pandas"}
    concat_func = "pandas:concat"
    output_instance = "pandas:DataFrame"


class PandasParquet(Pandas):
    concat_func = None  # pandas concats for us
    implements = {datatypes.Parquet}
    optional_imports = {"fastparquet", "pyarrow"}
    func = "pandas:read_parquqet"
    url_arg = "path"


class DaskParquet(BaseReader):
    imports = {"dask.dataframe"}
    implements = {datatypes.Parquet}
    optional_imports = {"fastparquet", "pyarrow"}
    func = "dask.dataframe:read_parquet"
    url_arg = "path"
    output_instance = "dask.dataframe:DataFrame"


class DuckDB(BaseReader):
    imports = {"duckdb"}
    output_instance = "duckdb.DuckDBPyRelation"  # can be converted to pandas with .df
    implements = {datatypes.Parquet, datatypes.CSV, datatypes.JSONFile, datatypes.SQLQuery}

    def glimpse(self, **kwargs):
        return self.read().limit(10)

    def func(self, **kwargs):
        import duckdb

        conn = getattr(self.data, "conn", {})  # only SQL type normally has this
        if isinstance(conn, str):
            conn = {"database": conn}
        self._dd = duckdb.connect(**conn)  # connection must be cached for results to be usable
        queries = {
            datatypes.Parquet: "SELECT * FROM read_parquet('{self.data.url}')",
            datatypes.CSV: "SELECT * FROM read_csv_auto('{self.data.url}')",
            datatypes.JSONFile: "SELECT * FROM read_json_auto('{self.data.url}')",
            datatypes.SQLQuery: "{self.data.query}",
        }
        return self._dd.query(queries[type(self.data)].format(**locals()))


class PandasDuck(Pandas):
    imports = {"duckdb", "pandas"}
    implements = {datatypes.Parquet, datatypes.CSV, datatypes.JSONFile, datatypes.SQLQuery}

    def __init__(self, data):
        self.d = DuckDB(data)

    def glimpse(self, **kwargs):
        return self.d.glimpse().df()

    def read(self, **kwargs):
        return self.d.read().df()


class Awkward(BaseReader):
    imports = {"awkward"}
    output_instance = "awkward:Array"


class AwkwardParquet(Awkward):
    implements = {datatypes.Parquet}
    imports = {"awkward", "pyarrow"}
    func = "awkward:from_parquet"
    url_arg = "path"

    def glimpse(self, **kwargs):
        kwargs["row_groups"] = [0]
        return self.read(**kwargs)


class PandasCSV(Pandas):
    implements = {datatypes.CSV}
    func = "pandas:read_csv"
    url_arg = "filepath_or_buffer"

    def glimpse(self, **kwargs):
        kw = {"nrows": 10, self.url_arg: self.data.filelist[0]}
        kw.update(kwargs)
        return self.read(**kw)


def recommend(data, check_imports=False):
    """Show which readers claim to support the given data instance"""
    out = set()
    for cls in subclasses(BaseReader):
        if type(data) in cls.implements:
            if check_imports and not cls.check_imports:
                continue
            out.add(cls)
    return out